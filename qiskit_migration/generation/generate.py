"""Pluggable code-transformation generator (Gemini | Claude | Ollama | OpenAI-compatible).

Given the user's old code plus authoritative deprecation records and retrieved
documentation, the configured LLM returns a *structured* `LLMTransformOutput`
(ported code + per-change rationale + warnings). Provider is selected by
`LLM_PROVIDER` (gemini | anthropic | ollama | openai). The `openai` provider is any
OpenAI-compatible endpoint (Groq, OpenRouter, Cerebras, GitHub Models, …) — one driver
for the whole ecosystem, several with free tiers. All share the same prompt and schema,
so output is interchangeable and the eval can compare them directly.

Provider SDKs are imported lazily inside each generator, so this module loads
without either installed.
"""

from __future__ import annotations

import re
from typing import Protocol

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from qiskit_migration.config import get_settings
from qiskit_migration.migration.deprecations import DeprecationRecord
from qiskit_migration.migration.models import LLMTransformOutput

_SYSTEM = """You are a Qiskit migration expert. You port Python code written for an
older version of Qiskit so it runs correctly on Qiskit {target_version}.

Rules:
* Use ONLY the AUTHORITATIVE DEPRECATIONS and REFERENCE DOCS provided. Do not invent
  APIs or rely on memory that contradicts them.
* The AUTHORITATIVE DEPRECATIONS are exact symbol->replacement mappings; apply EVERY one
  that appears in the code.
* Preserve the program's behavior and structure; change only what the migration requires.
* If a symbol's migration is not covered by the provided context, leave that code as-is
  and add a clear entry to `warnings` explaining what you could not migrate.
* Produce complete, runnable code (keep imports consistent with the new APIs).
* For every change you make, add a `changes` entry citing the source when available.
"""

_HUMAN = """TARGET QISKIT VERSION: {target_version}
SOURCE VERSION (hint, may be empty): {source_version}

=== AUTHORITATIVE DEPRECATIONS (apply these) ===
{deprecations}

=== REFERENCE DOCS (for grounding concrete code) ===
{context}

=== PREVIOUS ATTEMPT FEEDBACK (if present, your last output failed — fix it) ===
{feedback}

=== OLD CODE TO MIGRATE ===
```python
{code}
```
"""


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Retry transient LLM failures (overload / rate limit), not hard errors."""
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "503",
            "unavailable",
            "high demand",
            "overloaded",
            "429",
            "resource_exhausted",
            "deadline",
            "timeout",
            "500",
            "internal",
        )
    )


def _format_deprecations(deps: list[DeprecationRecord]) -> str:
    if not deps:
        return "(none detected by static analysis)"
    lines = []
    for d in deps:
        repl = d.replacement or "no direct replacement"
        lines.append(
            f"- `{d.symbol}` [{d.status}] -> {repl} "
            f"(deprecated {d.since_version}, removed {d.removed_in}; source: {d.source}). {d.note}"
        )
    return "\n".join(lines)


def _format_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(no documentation retrieved)"
    blocks = []
    for c in chunks:
        src = c.get("source", "unknown")
        blocks.append(f"[source: {src}]\n{c.get('text', '')}")
    return "\n\n---\n\n".join(blocks)


def _strip_code_fences(code: str) -> str:
    """Remove a wrapping ```python ... ``` block — some models emit one despite the schema."""
    stripped = code.strip()
    if not stripped.startswith("```"):
        return code
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _postprocess(out: LLMTransformOutput) -> LLMTransformOutput:
    out.ported_code = _strip_code_fences(out.ported_code)
    return out


# Text-mode (no structured output): for chat/open models that can't do tool/JSON output
# (e.g. DeepSeek-V3 on some endpoints). Ask for the migrated file in a single code block,
# extract it, and let the validators + sandbox verify it — at the cost of the per-change rationale.
_TEXT_INSTRUCTION = (
    "\n\nOUTPUT FORMAT: Respond with ONLY the complete migrated Python file inside a single "
    "```python code block. No explanation, no prose, and no JSON outside the code block."
)
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code_block(text: str) -> str:
    """Pull the migrated file out of a free-text/markdown response (first fenced block)."""
    match = _CODE_BLOCK_RE.search(text or "")
    if match:
        return match.group(1).rstrip()
    return _strip_code_fences(text or "").strip()


def _text_to_output(text: str) -> LLMTransformOutput:
    """Wrap a free-text model response as an LLMTransformOutput (text mode)."""
    return LLMTransformOutput(
        ported_code=_extract_code_block(text),
        changes=[],
        warnings=[
            "Generated in text mode: no structured per-change rationale "
            "(the configured model does not support tool/JSON-schema output)."
        ],
    )


def _prompt_vars(
    code: str,
    deprecations: list[DeprecationRecord],
    context_chunks: list[dict],
    source_version: str | None,
    feedback: str | None,
    target_version: str,
) -> dict:
    return {
        "target_version": target_version,
        "source_version": source_version or "",
        "deprecations": _format_deprecations(deprecations),
        "context": _format_context(context_chunks),
        "feedback": feedback or "(none)",
        "code": code,
    }


class Generator(Protocol):
    def transform(
        self,
        code: str,
        deprecations: list[DeprecationRecord],
        context_chunks: list[dict],
        source_version: str | None = None,
        feedback: str | None = None,
    ) -> LLMTransformOutput: ...


class GeminiGenerator:
    """Google Gemini via langchain structured output."""

    def __init__(self) -> None:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_google_genai import ChatGoogleGenerativeAI

        settings = get_settings()
        if not settings.gemini_api_key:
            raise ValueError("Missing GEMINI_API_KEY in environment/.env")
        self.target_version = settings.qiskit_target_version
        llm = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", _HUMAN)])
        self.chain = prompt | llm.with_structured_output(LLMTransformOutput)

    @retry(
        retry=retry_if_exception(_is_transient_llm_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=40),
        reraise=True,
    )
    def transform(
        self,
        code: str,
        deprecations: list[DeprecationRecord],
        context_chunks: list[dict],
        source_version: str | None = None,
        feedback: str | None = None,
    ) -> LLMTransformOutput:
        variables = _prompt_vars(
            code, deprecations, context_chunks, source_version, feedback, self.target_version
        )
        return _postprocess(self.chain.invoke(variables))


class AnthropicGenerator:
    """Anthropic Claude via the official SDK's structured-output `messages.parse`."""

    _MAX_TOKENS = 8192

    def __init__(self) -> None:
        import anthropic

        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError(
                "Missing ANTHROPIC_API_KEY in environment/.env "
                "(Developer Platform key — separate from a Claude Max subscription)."
            )
        # The SDK auto-retries 429/5xx with exponential backoff.
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=5)
        self.model = settings.anthropic_model
        self.target_version = settings.qiskit_target_version

    def transform(
        self,
        code: str,
        deprecations: list[DeprecationRecord],
        context_chunks: list[dict],
        source_version: str | None = None,
        feedback: str | None = None,
    ) -> LLMTransformOutput:
        variables = _prompt_vars(
            code, deprecations, context_chunks, source_version, feedback, self.target_version
        )
        message = self._client.messages.parse(
            model=self.model,
            max_tokens=self._MAX_TOKENS,
            system=_SYSTEM.format(**variables),
            messages=[{"role": "user", "content": _HUMAN.format(**variables)}],
            output_format=LLMTransformOutput,
        )
        return _postprocess(message.parsed_output)


class OllamaGenerator:
    """Local Ollama (e.g. qwen2.5-coder) via langchain structured output. Free, offline."""

    def __init__(self) -> None:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_ollama import ChatOllama

        settings = get_settings()
        self.target_version = settings.qiskit_target_version
        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", _HUMAN)])
        self.chain = prompt | llm.with_structured_output(LLMTransformOutput)

    @retry(
        retry=retry_if_exception(_is_transient_llm_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        reraise=True,
    )
    def transform(
        self,
        code: str,
        deprecations: list[DeprecationRecord],
        context_chunks: list[dict],
        source_version: str | None = None,
        feedback: str | None = None,
    ) -> LLMTransformOutput:
        variables = _prompt_vars(
            code, deprecations, context_chunks, source_version, feedback, self.target_version
        )
        return _postprocess(self.chain.invoke(variables))


class OpenAICompatibleGenerator:
    """Any OpenAI-compatible endpoint via langchain structured output.

    One generator for the whole OpenAI-API-speaking ecosystem — Groq, OpenRouter, Cerebras,
    GitHub Models, NVIDIA, Together, … (many with a free tier). Point it at a provider with
    ``OPENAI_BASE_URL`` + ``OPENAI_API_KEY`` + ``OPENAI_MODEL``. Use a model that supports
    tool/function calling so ``with_structured_output`` returns a validated ``LLMTransformOutput``
    (GPT-4o, Llama-3.3-70B, Qwen3-Coder, …); switch the structured-output method per provider
    via ``OPENAI_STRUCTURED_METHOD`` if needed.
    """

    def __init__(self) -> None:
        settings = get_settings()
        # Check config before importing the SDK, so a missing key is a clear ValueError rather
        # than a ModuleNotFoundError (the trap that previously broke CI for anthropic).
        if not settings.openai_api_key:
            raise ValueError(
                "Missing OPENAI_API_KEY in environment/.env (any OpenAI-compatible key: "
                "Groq / OpenRouter / Cerebras / GitHub Models). "
                "Also set OPENAI_BASE_URL + OPENAI_MODEL."
            )
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        self.target_version = settings.qiskit_target_version
        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            temperature=0,
        )
        self._text_mode = settings.openai_structured_method == "text"
        if self._text_mode:
            # Plain text completion + code-block extraction, for models without tool/JSON output.
            from langchain_core.output_parsers import StrOutputParser

            prompt = ChatPromptTemplate.from_messages(
                [("system", _SYSTEM + _TEXT_INSTRUCTION), ("human", _HUMAN)]
            )
            self.chain = prompt | llm | StrOutputParser()
        else:
            prompt = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", _HUMAN)])
            self.chain = prompt | llm.with_structured_output(
                LLMTransformOutput, method=settings.openai_structured_method
            )

    @retry(
        retry=retry_if_exception(_is_transient_llm_error),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=40),
        reraise=True,
    )
    def transform(
        self,
        code: str,
        deprecations: list[DeprecationRecord],
        context_chunks: list[dict],
        source_version: str | None = None,
        feedback: str | None = None,
    ) -> LLMTransformOutput:
        variables = _prompt_vars(
            code, deprecations, context_chunks, source_version, feedback, self.target_version
        )
        result = self.chain.invoke(variables)
        if self._text_mode:
            return _text_to_output(result)
        return _postprocess(result)


def get_generator() -> Generator:
    provider = get_settings().llm_provider.lower()
    if provider == "gemini":
        return GeminiGenerator()
    if provider == "anthropic":
        return AnthropicGenerator()
    if provider == "ollama":
        return OllamaGenerator()
    if provider in ("openai", "openai_compatible"):
        return OpenAICompatibleGenerator()
    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r} (use 'gemini', 'anthropic', 'ollama', or 'openai')."
    )


# Backwards-compatible alias (older imports referenced QiskitGenerator).
QiskitGenerator = GeminiGenerator

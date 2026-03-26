"""
Context Window Manager — macOS Agent Tooling Phase 2
Manages token budgets and context truncation.
"""

from __future__ import annotations

from typing import List

import tiktoken


class ContextWindowManager:
    """
    Manages the context window for LLM prompts.

    Responsibilities:
    1. Count tokens using tiktoken (cl100k_base — GPT-4 compatible)
    2. Build a final message list respecting max_tokens budget
    3. Apply middle truncation when context exceeds budget

    Priority order (high → low):
      system prompt > user input > memories > messages
    """

    def __init__(self, max_tokens: int = 8192, model: str = "cl100k_base"):
        self.max_tokens = max_tokens
        self._enc = None
        self._model = model
        try:
            self._enc = tiktoken.get_encoding(model)
        except Exception:
            # Fallback: rough char-based estimate (4 chars ≈ 1 token)
            self._enc = None

    def count_tokens(self, text: str) -> int:
        """Count tokens for a text string."""
        if self._enc is not None:
            return len(self._enc.encode(text))
        return len(text) // 4

    def build_context(
        self,
        system: str,
        memories: list[str],
        messages: list[dict],
        user_input: str,
    ) -> tuple[list[dict], int]:
        """
        Build a token-budget-aware message list.

        Args:
            system:       System prompt string
            memories:     Retrieved memory strings (already top-k filtered)
            messages:     Conversation history [{"role": ..., "content": ...}]
            user_input:   Current user input

        Returns:
            (filtered_messages, total_tokens)
            filtered_messages includes system + memories + user input + filtered history
        """
        final_messages: list[dict] = []
        total_tokens = 0

        # 1. System prompt (fixed priority)
        system_tokens = self.count_tokens(system)
        total_tokens += system_tokens
        final_messages.append({"role": "system", "content": system})

        # 2. User input (fixed priority)
        input_tokens = self.count_tokens(user_input)
        total_tokens += input_tokens

        # 3. Memories (fill remaining budget, top-down)
        selected_memories: list[str] = []
        remaining = self.max_tokens - total_tokens
        for mem in memories:
            mem_tokens = self.count_tokens(mem) + 10  # overhead for formatting
            if remaining - mem_tokens >= 0:
                selected_memories.append(mem)
                remaining -= mem_tokens
            else:
                break

        if selected_memories:
            memory_content = "Relevant memories:\n" + "\n".join(
                f"- {m}" for m in selected_memories
            )
            final_messages.append({"role": "system", "content": memory_content})
            total_tokens += self.count_tokens(memory_content)

        # 4. Messages (fill remaining, from newest to oldest)
        selected_messages: list[dict] = []
        remaining = self.max_tokens - total_tokens - input_tokens
        # Start from the most recent messages
        for msg in reversed(messages):
            msg_text = f"{msg['role']}: {msg['content']}"
            msg_tokens = self.count_tokens(msg_text) + 4  # role overhead
            if remaining - msg_tokens >= 0:
                selected_messages.append(msg)
                remaining -= msg_tokens
            else:
                break

        # selected_messages are newest-first; reverse to chronological
        selected_messages = list(reversed(selected_messages))
        final_messages.extend(selected_messages)
        total_tokens += sum(
            self.count_tokens(f"{m['role']}: {m['content']}") + 4
            for m in selected_messages
        )

        # 5. User input message
        final_messages.append({"role": "user", "content": user_input})

        # 6. If still over budget → apply middle truncation
        if total_tokens > self.max_tokens:
            final_messages, total_tokens = self._middle_truncate(
                final_messages, total_tokens
            )

        return final_messages, total_tokens

    def _middle_truncate(
        self,
        messages: list[dict],
        total_tokens: int,
    ) -> tuple[list[dict], int]:
        """
        Apply middle truncation: keep system + most recent + oldest,
        drop messages from the middle.

        Preserves:
          - All system messages (first 1)
          - Most recent N messages (last 1)
          - Oldest message (first non-system message)
        """
        # Always keep the first message (system)
        # Always keep the last message (user input or last assistant)
        if len(messages) <= 2:
            # Can't truncate further — just clip to budget
            return self._simple_truncate(messages, total_tokens)

        system_msg = messages[0]
        last_msg = messages[-1]
        middle_messages = messages[1:-1]

        excess = total_tokens - self.max_tokens
        kept: list[dict] = []

        # Try to fit middle messages back, newest first (P0-1 fix)
        for msg in reversed(middle_messages):
            msg_tokens = self.count_tokens(msg["content"]) + 4
            if excess <= 0:
                break
            excess -= msg_tokens
            kept.append(msg)
        kept = list(reversed(kept))

        # Build final list: system + kept middle + last
        truncated_messages = [system_msg] + kept + [last_msg]

        new_total = sum(
            self.count_tokens(f"{m['role']}: {m['content']}") + 4
            for m in truncated_messages
        )

        return truncated_messages, new_total

    def _simple_truncate(
        self,
        messages: list[dict],
        total_tokens: int,
    ) -> tuple[list[dict], int]:
        """Simple truncation: keep first and last messages only."""
        if len(messages) <= 1:
            # Just truncate the content of the single message
            msg = messages[0]
            content = msg["content"]
            # Estimate how many chars we can keep
            avg_token_ratio = 4
            max_chars = (self.max_tokens - 50) * avg_token_ratio
            if len(content) > max_chars:
                content = content[:int(max_chars)] + "\n... (truncated)"
                return [{"role": msg["role"], "content": content}], self.max_tokens
            return messages, total_tokens

        system = messages[0]
        last = messages[-1]

        # Rebuild system with truncated content
        sys_tokens = self.count_tokens(system["content"])
        if sys_tokens > self.max_tokens - 100:
            chars = int((self.max_tokens - 100) * 4)
            system = {
                "role": "system",
                "content": system["content"][:chars] + "\n... (truncated)",
            }

        return [system, last], self.max_tokens

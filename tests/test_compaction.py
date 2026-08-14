"""M2k: the model context stops growing without bound, and the player gets a recap.

The defect this closes: `GameSession.history` was re-sent to the narrator in full
every turn — O(n) per turn, O(n²) over a campaign, with nothing capping it. These
are pure-logic tests: no DB, no API calls, no torch.
"""

from __future__ import annotations

import pytest

from dm_agent.orchestrator.compaction import (
    STORY_SO_FAR_HEADER,
    build_messages,
    build_recap,
    estimate_tokens,
    plan_cut,
    render_transcript,
    summarize_history,
    turn_starts,
    with_cache_breakpoint,
)


def history(turns: int, *, tools_on: set[int] | None = None) -> list[dict]:
    """A realistic history: one player message per turn, an assistant reply, and
    a tool round-trip on the turns named in `tools_on`."""
    tools_on = tools_on or set()
    out: list[dict] = []
    for i in range(turns):
        out.append({"role": "user", "content": f"player action {i}"})
        if i in tools_on:
            out.append(
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"t{i}", "name": "roll_dice", "input": {}}],
                }
            )
            out.append(
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "17"}],
                }
            )
        out.append({"role": "assistant", "content": [{"type": "text", "text": f"narration {i}"}]})
    return out


# --- where it is safe to cut ------------------------------------------------


def test_turn_starts_finds_only_player_messages():
    h = history(3, tools_on={1})
    # A tool_result batch is also role=user, but its content is a list — cutting
    # there would orphan it from the tool_use it answers.
    assert turn_starts(h) == [0, 2, 6]
    assert all(isinstance(h[i]["content"], str) for i in turn_starts(h))


def test_plan_cut_leaves_a_short_session_alone():
    assert plan_cut(history(4), covered=0, threshold_tokens=10) is None  # too few turns
    assert plan_cut(history(40), covered=0, threshold_tokens=10**9) is None  # under budget


def test_plan_cut_returns_a_turn_boundary_keeping_the_recent_window():
    h = history(20, tools_on={3, 11})
    cut = plan_cut(h, covered=0, threshold_tokens=10, keep_turns=8)
    starts = turn_starts(h)
    assert cut == starts[-8]
    assert isinstance(h[cut]["content"], str)  # never mid-turn
    assert len(turn_starts(h[cut:])) == 8  # exactly the window we asked to keep


def test_plan_cut_never_moves_backwards():
    h = history(20)
    starts = turn_starts(h)
    # Already compacted past the point this call would choose → nothing to do.
    assert plan_cut(h, covered=starts[-4], threshold_tokens=10, keep_turns=8) is None


def test_estimate_tokens_grows_with_the_history():
    assert estimate_tokens(history(20)) > estimate_tokens(history(4)) > 0


# --- what goes on the wire --------------------------------------------------


def test_build_messages_without_a_summary_is_the_history_verbatim():
    h = history(5)
    assert build_messages(h, {}) == h
    assert build_messages(h, None) == h
    # A pointer with no summary behind it must not silently drop turns.
    assert build_messages(h, {"covered": 6}) == h


def test_build_messages_folds_the_summary_onto_the_first_kept_turn():
    h = history(10)
    covered = turn_starts(h)[-3]
    sent = build_messages(h, {"summary": "The party burned the barn.", "covered": covered})

    assert len(sent) == len(h) - covered  # the compacted turns are gone
    head = sent[0]
    assert head["role"] == "user"
    # No fabricated turn: the summary rides inside the first surviving message,
    # which keeps the roles alternating and the prefix byte-stable for the cache.
    assert head["content"][0]["text"].startswith(STORY_SO_FAR_HEADER)
    assert "burned the barn" in head["content"][0]["text"]
    assert head["content"][1]["text"] == h[covered]["content"]
    assert sent[1:] == h[covered + 1 :]
    # The last three turns survive word for word: the folded head plus two more.
    assert len(turn_starts(sent)) == 2
    assert "player action 7" in head["content"][1]["text"]


def test_build_messages_refuses_to_cut_at_a_non_boundary():
    # A pointer landing on a tool_result would orphan it — send everything instead.
    h = history(4, tools_on={1})
    bad = next(i for i, m in enumerate(h) if isinstance(m.get("content"), list))
    assert build_messages(h, {"summary": "notes", "covered": bad}) == h


def test_build_messages_does_not_mutate_the_persisted_history():
    h = history(6)
    before = [dict(m) for m in h]
    build_messages(h, {"summary": "notes", "covered": turn_starts(h)[-2]})
    assert h == before


# --- cache breakpoints ------------------------------------------------------


def test_cache_breakpoint_marks_the_last_block_and_copies():
    msg = {"role": "user", "content": "look around"}
    marked = with_cache_breakpoint(msg)
    assert marked["content"] == [
        {"type": "text", "text": "look around", "cache_control": {"type": "ephemeral"}}
    ]
    assert msg["content"] == "look around"  # the original is untouched

    multi = {"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    marked = with_cache_breakpoint(multi)
    assert "cache_control" not in marked["content"][0]
    assert marked["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in multi["content"])


def test_cache_breakpoint_leaves_thinking_blocks_alone():
    # Thinking blocks must be replayed exactly as received, so never decorate one.
    msg = {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm", "signature": "s"}]}
    assert with_cache_breakpoint(msg) == msg
    assert with_cache_breakpoint({"role": "user", "content": []}) == {"role": "user", "content": []}


# --- summarizing ------------------------------------------------------------


def test_render_transcript_keeps_prose_and_drops_mechanics():
    out = render_transcript(history(2, tools_on={0}))
    assert out.splitlines() == [
        "Player: player action 0",
        "DM: narration 0",
        "Player: player action 1",
        "DM: narration 1",
    ]


class _FakeClient:
    """Records the one call made and returns a canned message."""

    def __init__(self, text: str = "the notes"):
        self.text = text
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Block:
            type = "text"

        block = _Block()
        block.text = self.text
        return type("Msg", (), {"content": [block]})()


@pytest.mark.asyncio
async def test_summarize_history_folds_the_previous_summary_in():
    client = _FakeClient("the party burned the barn, then fled north")
    out = await summarize_history("they met the duke", "Player: run\nDM: you run", client=client)
    assert out == "the party burned the barn, then fled north"
    sent = client.calls[0]["messages"][0]["content"]
    # Rolling, not cumulative: the old summary is an input, so each compaction
    # costs the same no matter how long the campaign has run.
    assert "they met the duke" in sent and "you run" in sent


@pytest.mark.asyncio
async def test_summarize_history_of_nothing_makes_no_call():
    client = _FakeClient()
    assert await summarize_history("previous", "   ", client=client) == ""
    assert client.calls == []


@pytest.mark.asyncio
async def test_recap_reads_the_summary_and_only_the_recent_tail():
    client = _FakeClient("Previously: you burned a barn.")
    h = history(20)
    out = await build_recap(h, {"summary": "the distant past"}, recent_turns=3, client=client)
    assert out == "Previously: you burned a barn."
    sent = client.calls[0]["messages"][0]["content"]
    assert "the distant past" in sent
    assert "player action 19" in sent  # the recent window...
    assert "player action 5" not in sent  # ...and not the whole campaign


@pytest.mark.asyncio
async def test_recap_of_an_unplayed_campaign_is_empty():
    client = _FakeClient()
    assert await build_recap([], {}, client=client) == ""
    assert client.calls == []

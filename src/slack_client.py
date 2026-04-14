"""Thin wrapper around the Slack scheduled-message endpoints we use."""
from __future__ import annotations

from slack_sdk import WebClient


class SlackClient:
    def __init__(self, token: str):
        self._client = WebClient(token=token)

    def list_scheduled_post_ats(self, channel: str) -> set[int]:
        """Return the set of `post_at` timestamps already scheduled in `channel`.

        Used for idempotency: any entry whose computed `post_at` is in this
        set is already queued and should be skipped.
        """
        result: set[int] = set()
        cursor: str | None = None
        while True:
            kwargs: dict = {"channel": channel, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = self._client.chat_scheduledMessages_list(**kwargs)
            for msg in resp.get("scheduled_messages", []) or []:
                if msg.get("channel_id") == channel and "post_at" in msg:
                    result.add(int(msg["post_at"]))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
        return result

    def schedule_message(
        self,
        channel: str,
        text: str,
        post_at: int,
        blocks: list | None = None,
    ):
        return self._client.chat_scheduleMessage(
            channel=channel,
            text=text,
            blocks=blocks,
            post_at=post_at,
        )

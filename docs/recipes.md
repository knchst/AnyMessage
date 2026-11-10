# Investigate your conversations with evidence

Use these prompts with a local MCP client connected to AnyMessage.
First try [the fictional Atlas project](demo.md), then use [normal setup](../README.md#install-and-set-up) for your own WeChat conversations.
Your AI client performs the analysis; AnyMessage supplies messages and supported local attachments.

## Check a specification change

Input: one conversation and the requirement you want to verify.

> Start with AnyMessage status and find the conversation I name. Read its recent messages and continue paging if the relevant decision is older. Read available attachments that support the requirement. Make a table of the previous requirement, the proposed change, and the last explicit approval. Include the sender, UTC time, and message ID for each claim. Distinguish proposals from approvals. List attachments you could not read and any history that remains unsearched. Do not treat an empty incomplete search as proof that a decision does not exist.

For the demo, name the Atlas pilot project and ask about its battery, quantity, and delivery date.
Manually checked expectations are:

| Requirement | Expected interpretation | Source |
| --- | --- | --- |
| Battery | 400 mAh was the baseline; Maya approved 500 mAh. | Messages `:1`, `:2`, `:3`, and the specification attached to `:4`. |
| Quantity | The pilot stays at 50. | Message `:3` and the specification. |
| Delivery | November 26 is proposed and awaits factory confirmation. | Message `:7` and the specification. |

Confirm that the answer cites the approval in `message/message_0.db:3`, sent by Maya at 2026-11-09 09:03 UTC.
A result that treats Chen's earlier proposal as the approval needs correction.

## List unresolved work

Input: one conversation and a project or order name.

> Start with status, select the conversation I name, and read the relevant discussion and available attachments. List unresolved questions, the message that establishes each question, any explicitly assigned owner, and the next information needed. Keep the person who sent a message separate from the person responsible for a task. Leave the owner unassigned when the discussion does not name one. Distinguish missing evidence from evidence that a task is complete. Include sender, UTC time, and message ID, and state the scope you actually checked.

For the demo, ask what must be resolved before releasing the Atlas pilot order.
The thickness, factory confirmation of delivery, and quote remain unresolved.
The drawing in message `:6` is unavailable, so its contents cannot establish the final thickness.
Messages `:5`, `:7`, and `:8` provide the questions; they do not explicitly assign every task to an owner.
Check that the answer does not invent an owner or describe the drawing's contents.

## Prepare for a project review

Input: one conversation and the review topic.

> Use AnyMessage to prepare a short review brief for the project I name. Start with status. Read the relevant conversation and available attachments. Include confirmed decisions, proposals awaiting approval, unresolved questions, and the evidence we still need. Cite each point with sender, UTC time, and message ID. Suggest questions for the review without sending messages or changing external records. Report the history range and attachment limitations you encountered.

For the demo, prepare the next Atlas pilot review.
The brief should retain the approved 500 mAh battery and 50-unit quantity, ask for the final quote and factory delivery confirmation, and request the unavailable drawing before approving thickness.
Compare these points with [the manual evidence table](demo.md#read-the-evidence).
Suggested questions are the AI's recommendations; the cited messages establish the underlying open questions.

## Search selected chats within a date range

Find the two conversation ids through `list_conversations`, then pass them explicitly.
AnyMessage 0.5.0 supports these MCP calls for the fictional demo:

```json
{"conversation_id":"atlas-demo@chatroom","start_time":1794182400,"end_time":1794268800}
```

Pass that object to `get_messages` to read November 9, 2026 UTC.
To search both project and supplier chats that day, call `search_messages` with:

```json
{"query":"500","conversation_ids":["atlas-demo@chatroom","atlas-supplier-demo@chatroom"],"start_time":1794182400,"end_time":1794268800,"scan_limit":100}
```

The demo returns three matches across the two chats and excludes the supplier's November 10 follow-up.
Use both `conversation_id` and `id` when citing each hit.
Choose exactly one selector, either `conversation_id` or `conversation_ids` with 1–8 distinct ids.
An empty list never means all chats. A missing local table fails the call.
Search matches literal decoded text, not attachment bodies or semantic similarity.

For a civil day in another timezone, convert local midnight and the next local midnight separately to UTC Unix seconds.
This also handles days that last 23 or 25 hours:

```python
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

day = date(2026, 11, 1)
zone = ZoneInfo("America/Los_Angeles")
start_time = int(datetime.combine(day, time.min, zone).timestamp())
end_time = int(datetime.combine(day + timedelta(days=1), time.min, zone).timestamp())
```

Pass integers rather than date strings, floating-point seconds, or booleans.
The start is inclusive and the end is exclusive. Either bound may be absent.
When both are present, start must be less than end.
If `next_cursor` remains, repeat the call with that cursor, the original query, selection, and bounds.
The selection order and page sizes may change.
The cursor marks the last inspected row, even when it did not match.
`scan_limit` is a shared budget across all selected chats; `scanned` and failure counters describe only the current call.
An empty incomplete page requires continuation before drawing a conclusion about older messages.

## Inspect a search hit

AnyMessage 0.6.0 adds `get_message_context` for an exact hit's nearest older and newer records.
Pass both fields from the returned hit; bare message ids can repeat across conversations.
For the Atlas project proposal returned by the selected-chat search, call:

```json
{"conversation_id":"atlas-demo@chatroom","message_id":"message/message_0.db:2","before":1,"after":2}
```

The chronological result contains the baseline `:1`, proposal `:2`, Maya's approval `:3`, and specification attachment `:4`.
`anchor_index=1` identifies the proposal; the nearby approval establishes who approved the change.
`has_more_before=false` and `has_more_after=true` describe the remaining local records.
Pass the specification's conversation id, message id, and returned attachment id to `read_attachment` to verify its cached content.

Each side defaults to 3 and accepts strict integers from 0 to 50.
A zero-count side still reports whether local records remain there.
Counts include unsupported and decode-failed records; the anchor appears once, and up to 101 records can be returned.
Context is confined to that conversation but has no date restriction inherited from the search.
Its local coverage and `snapshot_consistency="per_query"` do not establish complete history or an atomic snapshot.
An absent or non-unique target in an existing chat fails with `message_not_found`; it never substitutes a nearby record.
If the conversation has no local message table, the call fails with `conversation_not_found`.

## Verify the result

Open a cited conversation/message pair with `get_message_context` to inspect its neighbors.
For a broader history range, use `get_messages` and follow its returned cursor when more pages remain.
Use `list_attachments` and `read_attachment` for the specific message, then check the returned availability and verification state.
Abbreviated IDs in the first three recipes use the prefix `message/message_0.db:` and conversation `atlas-demo@chatroom`.
The first three recipes use the project's November 9 UTC messages; the supplier chat also has a November 10 follow-up.

Text search is bounded to the explicit selection and optional date range.
There is no account-wide search, semantic search, or attachment-content search.
Readiness does not establish that a local history or attachment set is complete.
For private conversations, the AI client may send returned content to its model provider.

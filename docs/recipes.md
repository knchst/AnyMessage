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

## Verify the result

Open the cited messages with `get_messages` and follow its returned cursor when more pages remain.
Use `list_attachments` and `read_attachment` for the specific message, then check the returned availability and verification state.
All abbreviated IDs in these examples use the prefix `message/message_0.db:` and conversation `atlas-demo@chatroom`.
The fixture timestamps are UTC on 2026-11-09.

Current text search is bounded and conversation-scoped.
There is no date-filter argument, semantic search, attachment-content search, or automatic cross-conversation search.
Readiness does not establish that a local history or attachment set is complete.
For private conversations, the AI client may send returned content to its model provider.

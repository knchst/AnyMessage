# Try AnyMessage and report the result

Try [the fictional project](demo.md) before connecting a real account.
Choose an AI client you already use and ask one of the [investigation recipes](recipes.md).
Check whether its answer identifies the right approval, distinguishes an unconfirmed proposal, and lists missing evidence.

For your own conversations, follow [setup](../README.md#install-and-set-up) and choose one project question.
Use a task you would otherwise solve by reading the chat and its attachments yourself.
Record whether you reached a useful, verifiable answer and where setup needed individual help.
If you return for a different investigation, report whether it happened within 14 days of first use.

[Submit trial feedback](https://github.com/knchst/AnyMessage/issues/new?template=pilot-feedback.yml).
English, Japanese, and Chinese feedback is welcome.
The form is public; describe private work with fictional examples and leave out source messages, keys, account identifiers, files, and personal paths.

## Interpret the trial

The initial target is 20 people who use WeChat for project work and already use an AI client on a Mac.
Keep demo-only trials separate from real-data investigations.
Count each participant once for first use, and track later reports as follow-ups rather than extra participants.

The planning targets are 14 people reaching a verifiable answer without individual help, 8 returning for a different investigation within 14 days, and 5 describing a concrete use case or improvement request.
These are hypotheses, not measured adoption rates.
Give each first-use participant the full 14-day follow-up window before classifying a non-return.
Include people who could not complete setup and participants who did not provide a follow-up.

Track results under [the trial issue](https://github.com/knchst/AnyMessage/issues/13).
The public launch can proceed once the runnable demo and limitations are ready; the full trial runs alongside it.

## Capture repository growth

Repository maintainers can save the current stars and rolling GitHub traffic with:

```sh
python3 scripts/measure_growth.py
```

This requires an authenticated GitHub CLI with access to the repository's traffic APIs.
The script makes read-only requests and appends one JSON object to `.audit/growth.jsonl`, which is ignored by Git.
If an API request fails, the script stops without recording an incomplete snapshot.
Use `--repo owner/name` and `--output PATH` to choose another repository or log.

GitHub traffic covers a rolling 14-day window.
Overlapping window totals and daily unique counts cannot be added to obtain unique people across the whole campaign.
Clones may include automation, and traffic does not establish installation, usage, or star attribution.
The snapshot does not read message data, instrument the client, or schedule a background watcher.
Record public post URLs and dates alongside the snapshots to compare timing without claiming causation.

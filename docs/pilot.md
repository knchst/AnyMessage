# Try AnyMessage and report the result

Choose one dataset for each report.
You can use a fictional project before connecting your own data.

| Dataset | Instructions |
|---|---|
| Fictional WeChat Atlas project | Follow [the Atlas demo](demo.md). |
| Your local WeChat conversations | Follow [WeChat setup](../README.md#install-and-set-up). |
| Fictional Telegram River export | Follow [the River investigation](telegram-export.md#follow-a-fictional-search-hit-to-its-approval). |
| Your own Telegram Desktop JSON export | [Export and start the server](telegram-export.md#export-and-start-the-server), then follow [the manual AI client configuration](telegram-export.md#connect-your-ai). |

Choose an AI client you already use and one of the [investigation recipes](recipes.md).
Ask a question you would otherwise solve by reading the conversation yourself.
Check whether the answer identifies the right approval, distinguishes an unconfirmed proposal, and lists missing evidence.
Telegram supports exported text, not reading attachment contents.
Record what happened on your first attempt with the selected dataset, including failed setup and any individual help.
Describe a later success separately so the first-attempt result stays accurate.
If you return to your own data for a different investigation, report whether it happened within 14 days of that dataset's first real-data attempt.

[Submit trial feedback](https://github.com/knchst/AnyMessage/issues/new?template=pilot-feedback.yml).
English, Japanese, and Chinese feedback is welcome.
The form is public. Use fictional or redacted examples and leave out source messages, keys, account identifiers, files, and personal paths.
Do not include usernames, private project names, IDs, exports, or raw logs.
Check the selected values before submitting. "Not reported" and "Not reported or unsure" preserve missing evidence.
If you try another dataset, report it separately and mark that report as a follow-up.
For a follow-up, include the earlier public trial issue number in the outcome if available.
An issue number is optional. No private identifier is needed.
Your first report can describe earlier attempts and later use. State that timing in the outcome.

## Interpret the trial

The original cohort is 20 people who use WeChat for project work and already use an AI client on a Mac.
Track results under [the trial issue](https://github.com/knchst/AnyMessage/issues/13).
The planning targets are 14 people reaching a verifiable first answer without individual help, 8 returning for a different investigation within 14 days, and 5 describing a concrete use case or improvement request.
These are hypotheses, not measured adoption rates.
Report Telegram observations separately as exploratory feedback, including observations from people already in the WeChat cohort.

Count each eligible person once in the original cohort, with the relevant WeChat evidence.
Selecting a dataset does not establish cohort eligibility or a new participant.
Track later observations as follow-ups. A first report is not proof of first use.
A source switch neither creates another person nor restarts the original WeChat follow-up window.
A first real-data attempt after a demo does not establish return use.
Check that a reported return concerns a different real investigation on the selected dataset.
Anchor its 14-day window to the first real-data attempt on that route, including failed setup, rather than issue creation or first successful connection.
Demo observations do not start a real-data window or count as real-data success.

Keep demo outcomes, explicit failures, pending windows, elapsed non-return, and missing evidence distinct.
Give each first-use participant the full 14-day window before classifying a non-return.
Once cohort eligibility is known, retain failed setup and missing outcomes in the denominator.
Report them as failure, pending, or unknown as the evidence supports.
A missing follow-up does not establish non-return.
Keep conflicting answers and unclear timing unclassified until the reporter clarifies.
An unlinked follow-up remains feedback with unknown participant linkage.
Do not infer another participant from an unlinked report.
Legacy "Both" meant the demo and local WeChat choices in the old form, but supplies no result attributable to one route.
Preserve that answer as reported unless the reporter clarifies. Do not reinterpret it as use of two sources.

These fictional examples show how to record observations.

| Observation | Interpretation |
|---|---|
| First tries Atlas and verifies an approval without help. | First report, WeChat demo, unaided demo answer. No real-data success. |
| First attempts local WeChat but cannot finish setup. Reports on day 2. | First report, own WeChat, setup failure, window pending. No later report leaves reuse unknown. |
| First tries River and finds a proposal but cannot verify approval. | First report, Telegram demo, connected without a verifiable answer, demo only. |
| The same River tester first tries their own Telegram export on day 8 and verifies an answer with help. | Follow-up, own Telegram, first-attempt answer with help. Reuse is not established. One person, no WeChat cohort entry. |
| That tester returns to their own Telegram export for another real investigation on day 12. | Follow-up to the real-data report, one exploratory Telegram return within four days. No new person or WeChat participant. |

The public launch can proceed once the runnable demo and limitations are ready. The full trial runs alongside it.

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
Referrers include only the top 10 sources in that window.
Overlapping window totals and daily unique counts cannot be added to obtain unique people across the whole campaign.
Clones may include automation, and traffic does not establish installation, usage, or star attribution.
The snapshot does not read message data, instrument the client, or schedule a background watcher.
Record public post URLs and dates alongside the snapshots to compare timing without claiming causation.

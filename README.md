# ARClass bot

ARClass bot is a QQ group Arcaea challenge bot. It keeps the challenge rules in
framework-independent Python modules, while the NoneBot adapter only maps QQ
messages to those core APIs.

The default rule set runs from `上级 veryeasy` through `里皆传`, followed by
the endless `超上级 inf`. Completed runs are recorded locally and can be shown
through query and leaderboard commands.

## Features

- Random, fixed-order, timed, and endless challenge modes.
- HP-based and total-score-based clear rules.
- Yurisaki `/a recent text` parsing with trusted sender checks.
- Yurisaki `/a song` unavailable-chart handling for random and endless
  challenges.
- Bilingual official title matching from `songs.json` aliases.
- Quon/Genesis disambiguation with Play Potential when Yurisaki returns an
  ambiguous title.
- Group forwarded-message output for long query, rank, help, and challenge-list
  views, with plain-text fallback.
- Process-local maintenance mode for administrators.

## Commands

Mention the bot in group chats before each command.

- `/help`: show usage help.
- `/cha <challenge name>`: start a challenge.
- `/cha list`: list available challenges.
- `/cha <challenge name> help`: show a challenge rule summary.
- `status`: show the current active challenge.
- `cancel`: stop the current challenge. The run is failed and no result is
  recorded.
- `finish`: settle timed challenges early.
- `/query`: show your challenge records.
- `/rank <challenge name>`: show a challenge leaderboard. Endless challenges
  rank by cleared chart count first.
- `score <score>`: manually provide a score only after the bot has already
  confirmed a matching recent-text chart but could not read the score.

Administrator commands require NoneBot `SUPERUSER` permission:

- `/set maintain`: block new challenge starts.
- `/set resume`: allow new challenge starts again.
- `/active`: show active sessions.
- `/help admin`: show administrator help.

## Yurisaki Integration

Runtime score submission is based on Yurisaki `/a recent text` replies. The
adapter only trusts messages from QQ `3889054356`; nicknames, group cards, and
user-provided text are not trusted.

A valid recent-text message must:

- be sent by the trusted Yurisaki account;
- mention exactly the active challenge user;
- include a `Chart:` line matching the expected chart;
- include a valid difficulty;
- include a valid `Score:` line, unless the user later uses the manual `score`
  fallback after chart confirmation.

For random and endless challenges, if the current target is unavailable, ask
Yurisaki for `/a song <song name>`. A matching unavailable reply switches the
current round to a new random target and resets the round timer.

## Files

- `songs.json`: supported chart database. Each entry has `name`, `level`,
  `difficulty`, `notes`, and optional official-title `aliases`.
- `challenges.json`: challenge definitions.
- `scoring.py`: pure score-to-fault reverse calculation.
- `challenge.py`: framework-independent challenge manager facade.
- `challenge_runtime.py`: challenge state transitions and settlement.
- `challenge_views.py`: query, rank, help, and result message formatting.
- `challenge_targets.py`: random/infinite target selection and chart-list helpers.
- `challenge_labels.py`: shared display labels and lightweight format helpers.
- `challenge_recent.py`: recent-text parsing and chart matching.
- `challenge_config.py`: challenge configuration loading and validation.
- `challenge_store.py`: local completed-run record store.
- `nonebot_challenge.py`: NoneBot / OneBot v11 adapter.
- `nonebot_main.py`: NoneBot entrypoint.

The bot creates `challenge_stats.json` for completed-run records at runtime.

## Setup

Create a Python environment, install dependencies, then run NoneBot:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-nonebot.txt
cp .env.example .env
python nonebot_main.py
```

Adjust `.env` for your OneBot v11 connection and NoneBot deployment. Keep local
deployment values in `.env`; this file is ignored by Git.

## Validation

Useful checks before deploying:

```bash
python3 -m json.tool songs.json
python3 -m json.tool challenges.json
python3 -m py_compile scoring.py challenge.py challenge_models.py challenge_recent.py challenge_config.py challenge_store.py nonebot_main.py nonebot_challenge.py
```

## License

MIT

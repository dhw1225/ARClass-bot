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
- Group-scoped collaborative `/guess` games with exact aliases, conservative
  fuzzy matching, and rendered comparison history.
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
- `/guess`: start one collaborative song guessing game in the current group.
- `<song title or alias>`: while a game is active, mention the bot and submit a
  guess. Ambiguous or low-confidence fuzzy results show candidates without
  consuming a round.
- `/guess stop`: reveal the answer and stop the current game. Only the starter,
  a group administrator/owner, or a configured superuser may stop it.
- `score <score>`: manually provide a score only after the bot has already
  confirmed a matching recent-text chart but could not read the score.

A guessing game allows 15 valid guesses. Ten minutes without a valid guess
ends the game and reveals the answer. Failed, ambiguous, candidate-only, and
repeated guesses neither consume a round nor refresh the timeout.

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
- `guess_game.py`: framework-independent guessing catalog, fuzzy matching, and
  process-local group game state.
- `guess_image.py`: PNG comparison-history rendering.
- `guess_songs.json`, `guess_community_aliases.json`, and
  `guess_aliases.json`: guessing metadata and reviewed aliases.
- `tools/update_guess_data.py` and `tools/update_guess_aliases.py`: offline
  validators and explicit snapshot refresh helpers.
- `assets/NotoSansCJKsc-Regular.otf`: bundled CJK font used by guess images.

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
python3 -m unittest discover -v
python3 -m py_compile *.py tools/update_guess_data.py tools/update_guess_aliases.py
python3 tools/update_guess_data.py --check
python3 tools/update_guess_aliases.py --check
```

The update helpers never contact a Wiki during `--check`. Network refreshes are
performed only when `--refresh` is supplied explicitly. Manual aliases remain
separate and are not rewritten by the community snapshot updater.

## License

The project source code is MIT licensed. Bundled fonts and community-derived
data retain their own terms; see `THIRD_PARTY_NOTICES.md` and the metadata in
the relevant snapshot files.

# Local-source integration inventory

The original clone's `origin` and the source setup records both identify
`tierrewwalessio-crypto/voices-of-history`. Live GitHub metadata confirmed that
repository is not a fork and its default branch is `main`. At integration time,
upstream main, the original source clone and the common base were all
`061c3eb323f370b6296c36fb054eea4493194983`.

The modified source was a separate `voices-of-history-chatgpt` directory without
Git metadata. Its text was compared against that base after normalizing line
endings. The original clone also had initial local dry-run/config changes, which
are subsumed by the later modified source. Neither source/installed copy was edited.

| Verified local code | Contribution |
| --- | --- |
| `main`, `game_state`, `memory`, `injector`, `functions`, `overlay`, `prompts` | Shared extensions and fixes with baseline gates, current representative/country UI, history isolation, plan/reload handling, dry run |
| `profiles`, adapted MD/CWIC/TFR integration | Explicit ideology/equipment/action adapters; native differences under `native/legacy` over the original mod |
| `leader_voice`, `profile_lore`, `tfr_lore` | Opt-in dialogue style and bounded local characterization; no bundled overhaul text |
| `unified_mod`, `native/shared`, `native/variants` | Separate generated owned installation; original native mod reused for unchanged files/assets |
| `safe_actions`, `unified_runtime` | Typed actions, context bounds, engine-result journal, no-action path and no automatic uncertain-delivery retry |
| `strategist`, `usage_budget` | Disabled-by-default production-review pilot and persistent request allowances |
| `chatgpt_auth`, `chatgpt_client`, `llm_client` | Optional explicit provider selection and verified sign-in/Responses protocol; baseline Player2 retained |
| `launcher`, `Launcher-Voices`, `VoicesLauncher.cs` | Saved-language/source/frozen improvements and portable optional Paradox bridge |
| 8 local test modules | Imported offline suite, adapted to generated sources and public defaults; additional baseline/migration/disabled-path regressions |

Integration fixes include portable configuration/data paths, lazy cloud imports,
explicit budget/personality gates, baseline prompt/config/native fingerprints,
old-save action filtering, campaign-and-tag plan matching, stale legacy reply
discarding, finite-number validation, explicit descriptor aliases, pinned userdir
and packaging support. The original tracked runtime database is removed from the
source tree; it is never replaced by local conversation data.

Excluded: credentials, user-data, runtime mode/language state, logs, databases,
savegames, personal bridge/configuration files, generated launcher backups,
virtual environments, executables, and machine-specific start/test command wrappers.
The wrappers' useful operations are available through documented portable CLIs.
Native artwork is reused only from the unchanged original repository. No unrelated
overhaul or game assets are added.

The local code has no direct Claude adapter, complete variable/idea export,
unrestricted AI-to-AI negotiation, or general strategic/tactical planner. The
implemented pilot chooses bounded temporary production emphasis for three AI
majors. These limits are documented rather than described as missing implemented
features. Native-game acceptance is pending for this contribution; automated tests
and historical local observations are separate evidence.

## Regular Millennium Dawn scope verification

The MD contribution targets regular `MillenniumDawn/Millennium-Dawn`, not OEM.
The profile, configuration/native folder names, exported tags, memory/settings
namespaces, descriptor selection, generated dependencies and source references
were revised for that distribution. Regular Workshop metadata and developer
descriptors are checked; OEM metadata/source cannot be substituted by an alias.

The eight equipment archetypes, five outlook mappings and three AI template roles
were independently verified against both the untouched regular checkout and
authoritative upstream. Shared mappings were retained only after those checks.
See [regular-MD evidence and limitations](MILLENNIUM_DAWN_COMPATIBILITY.md) and
`tools/verify_md_source.py`. Non-MD generated native outputs remain unchanged.

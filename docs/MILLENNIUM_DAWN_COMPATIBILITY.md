# Regular Millennium Dawn

The supported MD profile is **regular upstream Millennium Dawn**, from
[`MillenniumDawn/Millennium-Dawn`](https://github.com/MillenniumDawn/Millennium-Dawn),
Workshop item **2777392649**. OEM is not selected or treated as this profile.

The read-only source audit passed against the regular local checkout at
`2efae2f406d73346d85cb59f339a4f917c4b6869` and authoritative upstream at
`80fdb7476c2a8c4de9ef6d41299fd50ae481c499`. Both descriptors declare HOI4 `1.19.*`.
The relevant identifiers and source hashes are recorded in
[`regular-md-identifiers.json`](../tests/fixtures/regular-md-identifiers.json).
No MD source assets or localized biographies are bundled with Voices.

## Configuration and selection

Use `game_profile: "millennium_dawn"`, `config-unified-md.json` for the unified
runtime, or `config-md.json` for the broader legacy catalog. The generated legacy
adapter is `HOI4_AI_Voices_MD`; the unified adapter retains its separate universal
installation. The MD examples use `user-data/ai_diplo_md.sqlite3` and MD-specific
realism/pilot settings. Existing OEM conversation/settings records are not reassigned
to the regular-MD namespace.
Both examples start in dry run; the pilot starts disabled.

The selector recognizes `ugc_2777392649.mod` and the verified regular local alias
`MD_LOCAL_STANDARD.mod`. It also recognizes a renamed selected descriptor when its
Workshop ID and regular release/developer display name match. Multiple regular-MD
installations enabled together are rejected. The selected descriptor's actual
display name supplies the generated load-order dependency, so the developer and
Workshop titles are not confused. Only the selected base is used for references.

The expected names are `Millennium Dawn: A Modern Day Mod` and
`Millennium Dawn: Developer Version`. These names and Workshop ID are verified in
the real regular descriptors. An OEM descriptor is rejected even if it reuses
the regular Workshop ID. The launcher checks both the selected external descriptor
and the source directory's descriptor before reading regular-MD characterization.
Explicit alias mappings cannot bypass this identity check. Old OEM profile names
are not aliases for regular MD; choose the new regular entry explicitly.

Example installation, with a regular-MD playset:

```powershell
.\.venv\Scripts\python.exe unified_mod.py --userdir "C:/Users/YOU/Documents/Paradox Interactive/Hearts of Iron IV" --profile millennium_dawn --initial
.\.venv\Scripts\python.exe middleware/main.py --config middleware/config-unified-md.json
```

Enable regular MD followed by the matching Voices adapter. Direct source startup
can opt in to verified local characterization with `--characterization-source`.
Neither this setup nor the source audit modifies the regular MD checkout, installed
mod, playset or saves automatically.

## Verified data and limitations

| Adapter behavior | Regular upstream definition |
| --- | --- |
| Five exported political groups | `fascism` = Salafist, `communism` = Emerging, `democratic` = Western, `neutrality` = Non-Aligned, `nationalist` = Nationalist Outlook |
| Equipment archetypes | `infantry_weapons_type`, `artillery_equipment`, `AA_Equipment`, `L_AT_Equipment`, `H_AT_Equipment`, `cnc_equipment_type`, `convoy`, `land_drone_equipment_type` |
| Bounded AI production emphasis | `infantry`, `armor`, `ifv_mechanized` template roles |

These mechanics were checked against regular MD rather than inferred from an OEM
label. All eight equipment identifiers are actual archetypes, the five political
labels match regular English localization, and the three priority roles exist in
regular MD's generic AI templates. They therefore retain their verified mappings.
The diplomacy GUI remains an authored add-on using the engine's selected-country
context. Its visibility/placement still requires native acceptance with regular MD.

Run the read-only audit on a regular source checkout after an MD update:

```powershell
.\.venv\Scripts\python.exe tools/verify_md_source.py --source "D:/Repos/Millennium-Dawn"
```

The audit rejects wrong distribution metadata, missing/non-archetype equipment,
changed political labels, missing AI roles and a changed supported game version.
CI exercises the checker with synthetic identifier definitions and verifies the
committed real-source evidence matches the adapter's identifiers. It does not
download or require an installed copy of MD.

The restricted generic action catalogs remain deliberate: this does not implement
regular MD's budgets, GDP/debt, elections, leaders, custom laws or nuclear systems.
No arbitrary variables, active ideas or hidden information are invented from source
text. Live UI/export synchronization, plan choices, effects/save postconditions and
expiry, and pilot behavior still need a disposable **regular-MD** campaign. No native
game was run for this revision. Vanilla, CWIC and TFR generated native output is
unchanged by the MD correction, and the original vanilla/config/prompt regression
fingerprints remain in the suite.

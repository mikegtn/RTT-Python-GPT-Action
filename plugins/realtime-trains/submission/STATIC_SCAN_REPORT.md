# Realtime Trains MCP — static preflight scan report

**Updated:** 26 September 2026  
**Target endpoint:** `https://rail.mikegtn.net/mcp`  
**Branch:** `plugin-submission-draft`  
**Source remediation status:** **READY TO DEPLOY AND RUN PLATFORM SCAN**  
**Public submission status:** **NOT YET READY TO SUBMIT**

## Scope and limitation

This is a source- and contract-level preflight review. It is **not** the OpenAI
Platform portal's authoritative **Scan Tools** result. The submission portal is not
available as a writable action in this ChatGPT session, and the changes on this
branch have not yet been deployed to the production VPS.

The five source remediations requested for the public release are complete on this
branch. The remaining blockers are deployment, the real Platform scan/domain
challenge, listing assets/pages, publisher verification, and confirmation of data
and branding rights.

## Executive result

| Area | Status | Notes |
|---|---|---|
| Public authentication model | PASS IN SOURCE | MCP is public/no-auth; upstream RTT credential stays server-side |
| Tool security metadata | PASS IN SOURCE | Top-level `securitySchemes: [{"type":"noauth"}]` plus `_meta` mirror |
| Output schemas | PASS IN SOURCE | Per-tool model-facing result schemas replace the generic `{ok,...}` schema |
| Diagnostic evidence | PASS IN SOURCE | Request evidence is logged server-side and omitted from public MCP results |
| Skills strategy | PASS IN SOURCE | v0.1.0 is explicitly MCP-tools-only; no draft Skills extension is advertised |
| Deployment | PENDING | Deploy branch commit to the VPS and run the production verifier |
| OpenAI Platform Scan Tools | PENDING | Must be run against the deployed production endpoint |
| Domain verification | PENDING | Complete the portal-issued challenge |
| Listing material | PENDING | Support, terms, privacy verification, icon and demo |
| Publisher / data rights | PENDING | Verify Platform publisher identity and public-use/branding permissions |

## Remediation 1 — public no-auth transport

**Status: complete in source.**

The public `/mcp` route no longer accepts or requires an end-user bearer token or
OAuth flow. `ACTION_API_KEY` remains private on the server and is used only for
the sidecar-to-Action loopback request.

The retired owner-only OAuth implementation, tests and consent bridge have been
removed from this branch. The deployment script removes the old public OAuth proxy
routes and consent page. It deliberately leaves the old OAuth database/key on the
host during the transition so rollback remains possible.

A simple process-level anonymous request ceiling is also present; upstream
credentials, Action concurrency limits and RTT rate limits remain server-side.

## Remediation 2 — canonical no-auth security schemes

**Status: complete in source.**

Every MCP tool is published with both:

```json
"securitySchemes": [{"type": "noauth"}]
```

and the compatibility mirror:

```json
"_meta": {
  "securitySchemes": [{"type": "noauth"}]
}
```

The project remains pinned to MCP Python 1.30.0. Its `Tool` model permits extension
fields, allowing the OpenAI top-level `securitySchemes` field to be serialized
without changing the MCP dependency solely for this release.

The production verifier explicitly checks both declarations on all 12 tools.

## Remediation 3 — model-facing output schemas

**Status: complete in source; runtime validation still required after deployment.**

The previous generic schema:

```json
{
  "type": "object",
  "properties": {"ok": {"type": "boolean"}},
  "required": ["ok"],
  "additionalProperties": true
}
```

has been removed from the MCP catalog.

The public MCP contract now describes the result object for each tool. Stable
workflow fields are explicit, including departure-board fields, service identities,
journey coverage, last-report metadata, route-map URLs and snapshot fields.
Detailed TIGER output reuses the existing explicit TIGER result schema. Where an
upstream RTT or topology payload can legitimately evolve, only that nested portion
is left extensible rather than making the whole MCP result unconstrained.

The MCP boundary now returns the result object directly as `structuredContent`,
so `outputSchema` describes the object the model actually receives.

## Remediation 4 — remove routine diagnostic evidence

**Status: complete in source.**

Backend Action calls can still contain `requestEvidence` and composite
`sourceRequestEvidence` for operational logging and regression diagnostics.
The public MCP adapter does not return either field in successful
`structuredContent` or tool text.

The server instruction to preserve request evidence has been removed. Exact RTT
`uniqueIdentity` values remain model-visible because they are functional service
identifiers needed by detail, location and mapping follow-ups.

## Remediation 5 — first release is MCP-tools-only

**Status: complete in source.**

Version 0.1.0 does not advertise the draft MCP Skills extension and the public MCP
server no longer exposes the packaged skill files through generic resources.

The existing `plugins/realtime-trains/skills/` material remains in the repository
for local/package use and future migration. It is not part of the v0.1.0 public
MCP import contract.

This avoids coupling the first public release to the draft Skills extension.
A later plugin release can add native imported skills deliberately.

## Public tool inventory

The verifier expects exactly 12 tools:

1. `getNextDepartures`
2. `searchStationServices`
3. `getTigerServiceDetails`
4. `getServiceDetails`
5. `getRttApiInfo`
6. `getApiUsage`
7. `suggestRailRoute`
8. `getJourneyRoute`
9. `getRailMapSnapshot`
10. `findJourneys`
11. `getTrainLocation`
12. `getRouteDetails`

All tools remain non-destructive. The three map-artifact tools are intentionally
not marked read-only/idempotent because they create saved map or image artifacts.

## Regression coverage changed with this branch

The transport tests now verify that:

- MCP initialization succeeds without credentials.
- All 12 tools expose top-level and mirrored `noauth` security metadata.
- Every tool has a non-generic output schema.
- Public structured content is the result object itself.
- `requestEvidence` and `sourceRequestEvidence` are absent from public results.
- An untrusted Origin is still rejected.
- The private Action backend still refuses insecure remote destinations.

The production verifier now connects to `https://rail.mikegtn.net/mcp` without a
credential and checks:

- MCP initialization and the 12-tool inventory.
- `noauth` metadata and output schemas.
- Result-only structured content without diagnostic evidence.
- Aberdeen-to-Plymouth bounded journey search.
- Exact service details, last reported location and route details.
- Journey route map and PNG snapshot.
- Public accessibility of the resulting HTML map and PNG.

## Remaining non-source blockers

### 1. Deploy this branch

The production endpoint will continue to reflect the currently deployed build
until a commit from this branch is installed on the VPS. Run the updated
`deploy/update_mcp.sh <full-commit-sha>` and then the full
`deploy/verify_mcp.py` regression.

Do not run the authoritative Platform scan against the old OAuth deployment and
treat its result as representative of this branch.

### 2. Run the actual OpenAI Platform Scan Tools action

After deployment, create/update the **With MCP** draft using:

- Universal URL: `https://rail.mikegtn.net/mcp`
- Authentication: **None / public**
- Imported MCP skills: none for v0.1.0

Record the returned scan errors and warnings verbatim. Source remediation should
not be considered complete in production until this scan succeeds.

### 3. Complete domain verification

Serve exactly the portal-issued verification token at the required challenge
location and complete verification in the portal.

### 4. Finish listing/reviewer material

Still required or unverified:

- Public support/contact URL
- Public terms URL
- Publicly resolving and current privacy policy
- Final icon/logo and rights confirmation
- Demo recording URL
- Verified publisher identity and eligible Platform project
- Annotation justifications requested by the portal
- Five positive and three negative tests already drafted in
  `OPENAI_PLATFORM_DRAFT.md`

### 5. Confirm public data/brand permissions

Before submission, confirm that the applicable Realtime Trains plan/terms permit
the intended public plugin use and that required Realtime Trains attribution is
visible. Confirm the same for any other data, formation, map/tile or branding
assets used by the listing or generated results.

The listing must not imply an official OpenAI or Realtime Trains relationship
beyond the permissions actually held.

## Optional cleanup after the first successful public deployment

These are not blockers for the five requested remediations:

- Decide whether `getRttApiInfo` and `getApiUsage` add enough end-user value to
  remain in the public directory tool catalog.
- After the rollback window, remove the retired OAuth database/key from the host
  through a deliberate secrets-cleanup change.
- Consider upgrading MCP Python from 1.30.0 separately; do not combine that
  dependency migration with the publication change unless the Platform scan
  requires it.
- Add native imported skills in a later version once the desired skill contract
  is settled.

## Completion criteria for submission

The plugin is ready to submit when all of the following are true:

- The no-auth branch commit is deployed to production.
- The production verifier passes without credentials.
- Platform **Scan Tools** succeeds against the deployed endpoint.
- Domain ownership is verified.
- Privacy, support, terms, icon and demo are complete.
- Publisher identity/project eligibility is complete.
- Public-use and attribution obligations for underlying data/branding are
  confirmed.
- The exact deployed commit SHA is recorded in reviewer notes.

# Realtime Trains MCP — static preflight scan report

**Prepared:** 25 September 2026  
**Endpoint under review:** `https://rail.mikegtn.net/mcp`  
**Source reviewed:** `master` at the point `plugin-submission-draft` was created  
**Overall result:** **NOT READY FOR PUBLIC SUBMISSION**

## Scope and limitation

This is a source- and contract-level preflight review of the MCP server, OAuth implementation, local plugin package and tests. It is not the OpenAI Platform portal's authoritative **Scan Tools** result: the submission portal is not exposed as a writable tool in the current ChatGPT session, and the execution environment could not resolve the production hostname. No claim is made that a Platform draft has been saved or that the portal scan has passed.

The report deliberately separates verified source findings from likely portal outcomes.

## Executive result

| Severity | Count | Summary |
|---|---:|---|
| Blocker | 3 | Public authentication model; OAuth security-scheme placement; actual portal draft/scan and domain verification not completed |
| High | 3 | Broad output schemas; diagnostic evidence exposed to models; public listing/support/terms/demo requirements incomplete |
| Medium | 4 | Skills import protocol absent; local package uses static bearer configuration; publisher-facing tools in public catalog; brand/data authorisation needs confirmation |
| Pass | 8 | Twelve discoverable tools, bounded inputs, annotations, Streamable HTTP, structured content, OAuth discovery implementation, PKCE/resource checks, explicit rail-data limitations |

## Blockers

### B1 — OAuth is implemented as private-owner approval, not public-user authentication

**Evidence in source**

- The module describes itself as `Private-owner OAuth`.
- Authorisation is redirected to the publisher's private admin approval page.
- Issued tokens use the fixed subject `site-owner`.
- The redirect allow-list is intentionally limited to ChatGPT callbacks.

**Why this blocks a public plugin**

A public directory plugin must have an authentication path that ordinary intended users and reviewers can complete. The current flow requires publisher-admin approval and does not represent or onboard individual public users. It can support a personal/private connector, but it is not yet a generally usable public OAuth product.

**Resolution options**

1. For a read-only public rail plugin, expose the MCP endpoint without end-user authentication and enforce upstream secrets, rate limits and abuse controls entirely on the server; or
2. Implement a genuine public account/sign-in and consent flow where each user receives a distinct subject and can independently authorise access; or
3. Keep the owner-only OAuth flow and distribute the plugin privately rather than submitting it to the public directory.

### B2 — OAuth `securitySchemes` are only placed in tool `_meta`

**Evidence in source**

`list_tools()` injects:

```json
{
  "_meta": {
    "securitySchemes": [
      {"type": "oauth2", "scopes": ["rail:access"]}
    ]
  }
}
```

The tool descriptor does not expose the same scheme as a top-level `securitySchemes` field. Current OpenAI plugin guidance expects the canonical scheme at tool level, with `_meta.securitySchemes` retained as a compatibility mirror where needed.

**Likely portal impact**

The scanner may import the tools but fail to classify their authentication correctly, or may raise an authentication/schema warning. Runtime OAuth discovery and the 401 challenge are present, but they do not replace correct tool-level metadata.

**Required fix**

Expose the canonical OAuth scheme in the tool descriptor's top-level `securitySchemes` and mirror it in `_meta.securitySchemes`. Verify the exact shape against the MCP SDK version used by the server and upgrade the pinned SDK if the current `types.Tool` model cannot represent the field.

### B3 — Authoritative Platform draft, scan and domain challenge are not complete

The OpenAI Platform organisation/project, verified publisher identity, portal draft ID, portal scan output and portal-issued domain challenge token are not available through the current connected tools. These steps must be completed in the actual submission portal before the plugin can be submitted.

This is an operational blocker rather than a defect in the MCP source.

## High-priority findings

### H1 — Output schemas are too broad to describe actual structured content

Every tool advertises essentially:

```json
{
  "type": "object",
  "properties": {"ok": {"type": "boolean"}},
  "required": ["ok"],
  "additionalProperties": true
}
```

Actual results contain detailed `result`, `requestEvidence`, `sourceRequestEvidence`, errors, maps, services and route structures. The broad schema weakens tool selection, validation and reviewability.

**Required fix**

Define exact success and error output schemas for every public tool, or at minimum for coherent groups of tools. Keep returned `structuredContent` aligned with the declared schema.

### H2 — Diagnostic `requestEvidence` is deliberately exposed in ordinary tool results

The server logs request evidence and also returns it in model-visible structured content. Workflow tools generate fresh request IDs and include source request evidence. Server instructions explicitly tell the model to preserve it.

**Risk**

Public submission guidance discourages unnecessary trace IDs, request IDs and internal diagnostic payloads in user-facing/model-facing results. This also enlarges schemas and may disclose implementation detail without user benefit.

**Required fix**

- Keep request evidence in server logs by default.
- Return only evidence needed for genuine user-facing provenance.
- Put deep diagnostics behind a separately named, publisher-only tool or an explicit diagnostic mode that is excluded from the public catalog.
- Remove the instruction to preserve `requestEvidence` in routine responses.

### H3 — Required listing and review material is incomplete

Still needed or unverified:

- Public support URL
- Public terms URL
- Publicly resolving privacy policy
- Final icon/logo and rights confirmation
- Demo recording URL
- Verified publisher identity and eligible project
- Portal annotation justifications
- Portal domain challenge
- Review credentials or a public no-auth flow

The copy-ready values and placeholders are in `OPENAI_PLATFORM_DRAFT.md`.

## Medium-priority findings

### M1 — The server exposes skills as generic resources, not the current skills extension

The server implements `resources/list` and `resources/read` for `SKILL.md` and references. It does not implement `skills/list` and `skills/get`.

**Likely impact**

The tools should still be discoverable, and the skill can be read manually as a resource, but the Platform scanner is unlikely to import it as a native plugin skill automatically.

**Resolution**

Implement the supported skills extension, or upload/package the skill separately and treat the MCP submission as tool-only for version 0.1.0.

### M2 — Local plugin package and production submission auth models are inconsistent

`plugins/realtime-trains/.mcp.json` uses a static environment bearer token:

```json
{
  "headers": {
    "Authorization": "Bearer ${RTT_MCP_API_KEY}"
  }
}
```

The proposed Platform submission uses OAuth. This is acceptable for private local development, but the README and package should make the distinction explicit so a static publisher credential is never bundled or mistaken for a public user-auth method.

### M3 — Two publisher-facing diagnostic tools are in the public catalog

- `getRttApiInfo`
- `getApiUsage`

They are correctly marked read-only, but they have limited value to normal rail users and reveal service-operational metadata. Consider excluding them from the public catalog while retaining them for private diagnostics.

### M4 — Data-source and brand authorisation need documentary confirmation

The public listing should not imply an official relationship beyond what exists. Confirm the rights to use the product name, returned rail data, formations, map data, tiles and any logos or marks. Keep required attribution in the listing or responses.

## Passed checks

### P1 — Tool inventory is finite and stable

The production verifier and tests expect **12 tools**:

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

### P2 — Names, titles and descriptions are present

Each catalog entry contains a machine name, human-readable title, description and input schema.

### P3 — Input schemas are bounded

Inputs generally use:

- `additionalProperties: false`
- Required-field lists
- String length limits
- Integer minimums and maximums
- Enumerations
- Array item/count limits
- A strict 24-character hexadecimal map ID pattern
- Exact opaque service identities with a warning not to construct them

### P4 — Risk annotations are present on every tool

Every tool currently declares:

- `readOnlyHint`
- `destructiveHint`
- `idempotentHint`
- `openWorldHint`

The three map-artifact tools are deliberately not marked read-only or idempotent. No tool is marked destructive.

### P5 — Streamable HTTP and structured content are implemented

The server uses the MCP SDK's Streamable HTTP session manager, stateless mode and JSON responses. Tool results include both text content and structured content.

### P6 — OAuth discovery and challenge logic exist

Source includes:

- Protected-resource metadata
- OAuth authorisation-server metadata
- Dynamic client registration
- Authorisation, token and revocation endpoints
- `WWW-Authenticate` with resource metadata and `rail:access`

### P7 — OAuth protocol controls are strong for the current private flow

The implementation checks:

- PKCE S256 challenge format
- Exact MCP resource/audience
- Exact scope
- Exact callback allow-list
- Short-lived authorisation codes
- Hashed stored bearer secrets
- Access/refresh expiry
- Refresh-token rotation and family revocation
- Rate limits and request-body limits

### P8 — Rail-domain limitations are unusually explicit

The contracts correctly disclose that:

- Latest train location is a last actual timing report, not GPS.
- Forecasts are not observations.
- Journey search is bounded and not exhaustive.
- Official minimum interchange times are not verified.
- Route geometry between schedule points may be inferred.
- Infrastructure routes are not guaranteed passenger itineraries.
- Exact service identities must be preserved.

These are good review and user-trust characteristics.

## Expected portal scan outcome — forecast, not an actual result

Once a suitable public auth model is in place and the portal can authorise successfully, the scanner should discover 12 tools with titles, descriptions, bounded inputs and complete risk annotations. It is likely to produce warnings or failures around authentication metadata and broad output schemas until B2 and H1 are fixed. Native skill import is unlikely until M1 is addressed.

## Recommended remediation order

1. Decide whether this is a **private owner plugin** or a **public directory plugin**.
2. Replace owner-only OAuth with public no-auth access or a genuine public-user OAuth flow.
3. Add canonical top-level tool `securitySchemes` and retain the compatibility mirror.
4. Replace broad output schemas with exact schemas.
5. Remove routine diagnostic evidence from public outputs and instructions.
6. Decide whether to remove publisher diagnostics from the public catalog.
7. Implement native skills discovery or make version 0.1.0 explicitly MCP-tools-only.
8. Publish and verify privacy, support and terms pages; prepare icon and demo video.
9. Create the actual Platform draft, authorise it, run **Scan Tools**, record the returned errors/warnings verbatim, and fix source-side findings.
10. Complete domain verification and reviewer tests, then submit.

## Completion criteria

The plugin is ready to submit only when:

- Intended users can authenticate without publisher-admin access, or the public endpoint intentionally requires no user auth.
- Portal Scan Tools completes successfully against the production endpoint.
- Tool security schemes and output schemas pass validation.
- Every annotation requiring justification has one.
- Privacy, support, terms, logo, demo and publisher identity are complete.
- Domain ownership is verified.
- Exactly five positive and three negative review cases are entered.
- The source commit deployed to production is recorded in the submission notes.

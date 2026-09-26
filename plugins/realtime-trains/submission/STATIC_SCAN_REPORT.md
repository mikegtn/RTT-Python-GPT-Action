# TrainBrain publication preflight

Updated 26 September 2026 after reconciling the publication branch with the
production TrainBrain implementation. This is a source review, not an OpenAI
Platform Scan Tools result.

## Reconciled contract

- Fourteen tools, including service progress and native PNG schematics.
- Public no-auth endpoint, with canonical and mirrored noauth security metadata.
- Explicit model-facing output schemas; structuredContent is the result itself.
- Journey output supports up to four legs, connection details and search limits.
- TrainBrain branding, direct railway backend, isolated usage/maps and public
  image routes are retained.
- Request diagnostics remain server-side; exact RTT service identities remain in
  public results for follow-up calls.
- First public release is tools-only. Original reference documents remain in the
  package; no skill resources are advertised over the public MCP endpoint.
- Retired OAuth runtime, proxy routes and consent bridge are removed. The updater
  backs up configuration, the bridge and service unit, retains the previous
  release, and preserves the old private OAuth database/key for rollback.
- RTT and TIGER credentials remain in the protected server environment. The
  separate authenticated GPT Action retains its existing service and key.
- Global ceiling of 240 MCP requests per minute, four concurrent tool executions,
  bounded journey searches and request-size/deadline limits.

## Verification

Run the unit suite with both mcp and snapshots extras installed. Tests cover
anonymous transport, metadata, rejected origins, rate-limit recovery, schemas for
multi-change journeys, direct backend assets and native/public PNG equivalence.

After deployment, run deploy/verify_mcp.py without credentials, then its
--multi-interchange and --service-progress --progress-image modes. The verifier
checks advertised schemas against real results and downloads generated public
maps and images. Record the deployed SHA and live results separately.

## Remaining publication work

- Run the authoritative Platform Scan Tools against the deployed endpoint.
- Complete the portal-issued domain challenge.
- Confirm public privacy, support and terms pages, final artwork and demo video.
- Complete verified publisher/project details and review annotation justifications.
- Confirm public-use and attribution permissions for data, map tiles and branding.
- Record five positive and three negative reviewer tests from OPENAI_PLATFORM_DRAFT.md.

The listing uses TrainBrain as the independent assistant name and attributes
Realtime Trains as a data provider. Consider separately whether the aggregate
getApiUsage and getRttApiInfo tools belong in the final directory listing.

OpenAI reference: https://developers.openai.com/plugins/reference

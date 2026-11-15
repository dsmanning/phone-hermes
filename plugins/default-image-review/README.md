# Default-profile image review

Host-side Hermes plugin for installed Hermes 0.22.5 plugin APIs. No server, gateway, package installation, sandbox credentials, or auxiliary-vision calls.

## Deployment (on Chiron, default profile)

From the bundle's `plugins` directory:

```bash
bash install-default-image-review.sh \
  /media/athena/chiron/hermes-output \
  /media/athena/chiron/hermes-output/driveway-current.jpg \
  /media/athena/chiron/hermes-output/driveway-default-review.json
```

The script installs only this plugin under the default profile, refuses different existing plugin files, allowlists the specified host image directory, grants this plugin provider/model override permission (needed to pin the saved default route), enables the plugin, checks auxiliary vision is unchanged, and requests exactly one review. Tool callers cannot select provider/model. Existing trust allowlists are preserved and may reject a newly selected default; that is a deliberate fail-closed outcome.

The output path must not exist. The CLI reserves it before making the charged request. A failure may leave an empty/error report. Do not blindly rerun a failed submission with another output path; inspect it first. Provider-internal retries cannot be controlled by this plugin.

No gateway restart is performed by the installer. After the CLI test succeeds and its report has been checked, run `hermes gateway restart` to load the tool into messaging. A running conversation may also need a new tool-list snapshot.

## Routing and evidence limitations

Each call reads the active profile's persisted `model.default` and `model.provider`. This is not the current conversation's temporary model override. Only explicit non-auto model/provider settings are accepted. Actual local image bytes go into `ctx.llm.complete_structured`; no Luna description is passed.

A reported model/provider mismatch returns failure without a review. **The host facade does not expose full fallback history, and its model/provider labels may be route attribution rather than independently verified response metadata.** Reports therefore always include `default_model_verified: false` and an explicit fallback-status limitation. Successful tool execution alone must not be described as independently verified Sol execution.

If the requirement is attested no-fallback routing, additional Hermes host-facade support is needed. This plugin cannot guarantee that property through the currently installed API.

## File and error boundaries

- Explicit host image roots only; empty configuration denies all reads.
- Absolute paths only; descriptor-relative traversal refuses symlinks and parent traversal.
- Regular files only, maximum 10 MiB; JPEG/PNG signature checks (not a full image decoder).
- Image SHA-256/size recorded for correlation.
- Provider exceptions sanitized in tool output; inspect host diagnostics privately.
- Review content is returned as model-generated text, not executed.

Disable without deleting settings: `hermes plugins disable default-image-review`, then restart the gateway when safe. Auxiliary vision settings are not modified.

## Verification status

Repository unit tests cover pixel payload/default selection, root and file hardening, invalid inputs, reported-route mismatch, tool/CLI registration, sanitized failures, and simulated installer behavior. Installed API source has been inspected. Live Chiron installation/model execution still needs the deployment command above; unit tests do not establish live provider compatibility or model identity.

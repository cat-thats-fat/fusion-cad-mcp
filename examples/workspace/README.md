# Guarded blank-width build

This manifest changes a rectangular solid from 60 mm to 70 mm along root X and checks that its 5 mm thickness along root Z is preserved. It uses a small native seed archive that you create in Fusion.

## Prepare the seed

1. In a parametric Fusion design, add a length user parameter named `blankWidth` with expression `60 mm`.
2. Create a rectangle on root XY with X dimension driven by `blankWidth` and Y dimension `20 mm`. Extrude it `5 mm` along root Z as a new solid. Name the body `Blank`.
3. Finish the sketch and leave the design at the end of the timeline. Export a local self-contained Fusion Archive (`.f3d`).

If you adapt this fixture, use `workspace_bind` and `workspace_inspect` to check the exact parameter expression and the body extents before filling the manifest's baseline values. For the inspection call, use each check's `id`, `metric`, `entities`, `frame`, `units` and `axis` from the manifest.

## Run the build

Replace `<absolute-workspace>` with an absolute path on the Fusion host. Initialize it, copy the example manifest to its `design.json`, and put your archive at `assets/seed.f3d` inside it. [Example manifest](design.json)

```text
fusion-cad-workspace init "<absolute-workspace>"
```

With Fusion running and idle, build and inspect the checkpoint pointers.

```text
fusion-cad-workspace build "<absolute-workspace>" --branch blank
fusion-cad-workspace status "<absolute-workspace>"
```

Check `result.execution`, `result.verification` and the measurement rows. A passing width row reports 60 mm before the edit, 70 mm after it and a +10 mm delta, subject to the declared tolerances. A passing thickness row reports a near-zero delta. A verified archive is reopened and checked before `accepted` is published. A failed or incomplete candidate can be retained as `wip`.

A second build with the same manifest and seed can reuse the verified archive for a compatible Fusion and executor. `result.cache_hit` identifies this case and `construction_calls` reports how many parameter edit recipes ran. The current checks still run on a fresh copy.

## Restore and edit a copy

```text
fusion-cad-workspace restore "<absolute-workspace>" blank --kind accepted
```

Restore verifies the artifact hash and returns a handle to a fresh working document. Use `workspace_inspect` to collect its current baseline before making another edit. For an interactive edit use `workspace_edit`, or pass a JSON object with `workspace_path`, `contract` and optional `custom_script` to `fusion-cad-workspace edit REQUEST.json`. The workspace tool reference lists the contract fields. [Tool reference](../../src/fusion_cad_mcp/knowledge/tools.md#local-workspace-v1)

For a managed Python build, add a `script` field with a relative `.py` path inside the workspace. Its `run(_ctx)` must set the `blankWidth` parameter's expression to `"70 mm"` through the supplied Fusion design and can run other trusted native steps on the same copy. Independent readback verifies the declared checks. Managed Python builds execute their recipe each time.

Supply a new UUID up front with `--operation-id` to track a write even if its reply is lost. Inspect the ledger with `fusion-cad-workspace status "<absolute-workspace>" --operation-id UUID`. Use `recover` for a confirmed late guarded edit and keep unresolved writes fenced.

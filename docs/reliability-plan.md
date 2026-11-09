# Field-run reliability release

Approved scope, ordered by risk and practical benefit:

1. Preserve typed operation results in bounded local receipts. Serialize participating
   clients, refuse duplicate operation IDs with different inputs, and retain a write
   barrier after uncertain mutations. Never claim exactly-once execution or rollback.
2. Fix structured-result parsing separately from genuine missing stdout. Investigate
   the latter read-only, without replacing Autodesk writers or restarting user documents.
3. Add rigid-group membership preview, explicit descendant scope, limits and readback.
4. Add compact assembly inspection and bounded health filtering/pagination.
5. Add an editable constrained rectangle using existing primitive/mutation helpers.
6. Open local F3D archives into separate documents and publish validated PNG viewport
   files atomically. Retain staged export verification and expose operation receipts.
7. Synchronize discovery, tool reference and agent recipes; describe unsupported imports
   and measured verification limits honestly.

Verification: targeted input/fault/concurrency tests, full regression suite, wheel
installation, independent code review, and disposable live Fusion documents with
numeric geometry/parameter/occurrence readback. User design files are evidence only;
they are not test fixtures and must not be edited or published.

Stretch work (service-path/tool-envelope validation) follows only after the core
release is proven. A complete motion or cable-routing solver remains out of scope.

Completed items 1–7 in version 0.3.0. See [verification and limits](reliability-validation.md). The optional service-path pilot remains deferred to keep this release focused on proven recovery and reusable workflows.

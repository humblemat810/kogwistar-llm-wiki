# ACL And Source Revision Policy

This document defines how access control applies when one logical source has
multiple immutable revisions.

## Policy And Evidence Are Different

Raw source bytes and their revision documents are immutable evidence. ACL
records are append-only policy history. An ACL record may supersede an older
record, but supersession does not change the source bytes, revision identity,
spans, or provenance.

## ACL Version Resolution

For ordinary reads, retrieval, maintenance, embedding dereferencing, and
projection, authorization uses the newest active ACL record for the exact ACL
target. ACL version numbers are policy versions, not source revision numbers.

Version 2 does not walk an ACL ancestor chain or merge missing fields from
version 1. If version 2 should retain the owner, scope, or sharing rules from
version 1, the writer must copy those values explicitly into version 2.

Older ACL records remain available for audit and provenance, but they are not
fallback authorization. A historical grant must not restore access after a
later revocation.

## Source Revision Rules

A changed source creates a new immutable revision document. A new revision does
not implicitly inherit ACL settings from its predecessor merely because the
source URI, logical source ID, or timestamp is related.

The application must choose one of these explicit policy scopes:

- **Logical-source policy:** all retained revisions share one current ACL
  target. This is the default safety policy for ordinary document history. A
  revocation applies to old and new revisions immediately.
- **Revision policy:** each revision has its own ACL target. Use this only when
  different revisions intentionally have different sensitivity. The new ACL
  record must be written explicitly; timestamps never determine access.

The source revision ID remains an evidence identity in either case. It must not
be replaced by an ACL version, and ACL changes must not tombstone or rewrite
the raw source.

## Example

1. Revision 1 is created and Alice is granted access.
2. Revision 2 is created from updated bytes.
3. An administrator revokes Alice.

Under the default logical-source policy, Alice loses access to both revisions
for normal reads, even though revision 1 was accessible earlier. The previous
grant remains visible in ACL history for audit.

If revision 2 is intentionally public while revision 1 remains confidential,
the application must use revision-scoped ACL targets and assign both policies
explicitly. No resolver should infer that result from creation times.

## Historical Audit Access

An audit or legal workflow may need to answer “who could access this at policy
version X?” That requires an explicit, privileged `as_of` policy evaluation.
It must not be used by normal source reads, search, maintenance, embeddings, or
dereference paths, because it could re-expose data after revocation.

## Implementation Requirements

- Bind every source revision, parse view, embedding reference, and derived
  artifact to an explicit logical-source or revision ACL target.
- Authorize the current active policy before returning raw content or derived
  evidence.
- Preserve ACL supersession history without using it as an access fallback.
- Reject ambiguous ACL updates that do not identify their target scope.
- Test both current-policy revocation and intentionally revision-scoped access.

The current graph ACL resolver follows the latest-record rule for graph ACL
targets. Source lifecycle integration must make the policy binding explicit so
that raw revision-document reads cannot depend on implicit metadata defaults.

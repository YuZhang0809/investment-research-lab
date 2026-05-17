# Strategy Boundary

This repository is the public research engine. It may implement generic strategy
expression primitives, but it must not contain private strategy decisions.

## Public Engine Owns

The public engine may own reusable mechanics:

- configurable factor groups
- configurable score weighting
- generic rank, z-score, winsorization, and missing-value handling
- generic filters such as liquidity filters or factor tail exclusions
- portfolio construction constraints such as holdings count, lot size, and ADV
  caps
- walk-forward simulation, reporting, and validation code
- lightweight experiment protocols, run ledgers, and decision-note templates
  for recording what was tested and why
- public-safe example configs using synthetic or illustrative parameters
- tests built from synthetic fixtures

These capabilities should use neutral names such as `composite_score`,
`weighted_factor_score`, or `factor_tail_filter`. Names should describe the
mechanism, not a private research conclusion.

## Public Engine Must Not Own

The public engine must not contain:

- real private universe files
- real candidate lists or selected tickers
- real reports, final equity, tax, execution, or benchmark results
- real run ledgers, protocols, notes, or final research conclusions
- private parameter choices that represent a research conclusion
- private go/no-go decisions or paper-test decisions
- strategy names that reveal a live private implementation
- audit systems, compliance workflows, approvals, user roles, permissions, or
  immutable event stores

For example, a generic weighted scoring module is public-safe. A config that
states a private final allocation such as "use this value/quality weight on the
Japan small-cap universe" belongs in a private workspace.

A generic run ledger schema is public-safe. A ledger containing real run IDs,
private universes, benchmark results, decisions, or notes belongs in a private
workspace.

## Config Rule

Public configs are examples. They should demonstrate shape and syntax, not
encode private research decisions.

Private workspaces may pass real configs into the public engine at runtime. The
public engine should treat those configs as external inputs and should not
depend on their paths or contents.

## Return-To-Public Workflow

When private research exposes a missing engine capability:

1. Describe the missing capability in generic terms.
2. Implement the generic primitive in the public engine.
3. Add synthetic tests and public-safe documentation.
4. Run public validation and privacy scans.
5. Refresh the private engine snapshot.
6. Run real private configs and keep the resulting parameters, reports, and
   conclusions private.

This keeps the public repository useful as a research engine while protecting
the private workspace's real research assets.

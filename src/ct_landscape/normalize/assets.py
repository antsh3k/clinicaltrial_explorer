"""Interventions → assets (spec §5.1 steps 4–7): dedup-key grouping, otherNames alias merge with the
contested-alias veto, canonical-name choice. Pure Python over rows pulled from the raw tables.

Identity rules:
  - provisional asset = dedup_key of the cleaned intervention name (router in drug_names.py)
  - otherNames are first-party synonymy: an alias key claimed by ONE asset cluster → merge/assign;
    claimed by ≥2 distinct clusters after all uncontested merges → CONTESTED: logged, never applied
  - global alias uniqueness: one alias key → exactly one asset (asset_aliases.alias_key PRIMARY KEY)
  - combos keep component edges; a combo's key is recomputed from its components' roots after merging
  - no fuzzy matching, at all
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ct_landscape.normalize.drug_names import Keyed, display_surface, is_code_name, route

ASSET_TYPES = ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT", "GENETIC")
DOMINANCE_MIN_TRIALS = 5  # a claimant must assert the alias in at least this many trials …
DOMINANCE_RATIO = 10  # … and at least this many times more often than every other claimant


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # deterministic root choice: shorter key, then lexicographic — never insertion order
        keep, drop = sorted((ra, rb), key=lambda k: (len(k), k))
        self.parent[drop] = keep


@dataclass
class InterventionRow:
    nct_id: str
    intervention_no: int
    type: str
    name_raw: str


@dataclass
class AssetResult:
    """Everything build.py needs to write the asset tables."""

    # per (nct_id, intervention_no): list of (asset_id, via) — via ∈ {'name','combo_component'}
    intervention_assets: dict[tuple[str, int], list[tuple[str, str]]] = field(default_factory=dict)
    assets: dict[str, dict] = field(
        default_factory=dict
    )  # asset_id → {canonical_name, dedup_key, is_combo, n_trials}
    components: list[tuple[str, str]] = field(default_factory=list)  # (combo_asset_id, component_asset_id)
    aliases: dict[str, tuple[str, str, str]] = field(
        default_factory=dict
    )  # alias_key → (asset_id, alias_raw, source)
    contested: list[tuple[str, list[str], int, str]] = field(
        default_factory=list
    )  # (alias_key, asset_ids, n_trials)
    census: Counter = field(default_factory=Counter)
    gate_census: Counter = field(default_factory=Counter)
    other_name_gate_census: Counter = field(default_factory=Counter)
    dropped_parts_census: Counter = field(default_factory=Counter)


def _trim_surface(raw: str) -> str:
    """Display surface: the case-preserving clean (prefixes, doses, parentheticals, placebo clauses removed)."""
    return display_surface(raw) or raw.strip()


def build_assets(
    interventions: list[InterventionRow], other_names: dict[tuple[str, int], list[str]]
) -> AssetResult:
    res = AssetResult()
    uf = UnionFind()

    # ---- 0. vocabulary pass: single-token standalone keys with ≥2 trials become `known_tokens`, so that a
    # space-joined regimen ("lenalidomide dexamethasone") can be recognised as a combination in pass 1
    single_support: dict[str, set[str]] = defaultdict(set)
    first_pass: dict[str, Keyed] = {}
    for iv in interventions:
        k = first_pass.get(iv.name_raw)
        if k is None:
            k = first_pass[iv.name_raw] = route(iv.name_raw)
        if k.key and not k.is_combo and " " not in k.cleaned.strip():
            single_support[k.key].add(iv.nct_id)
    known_tokens = frozenset(k for k, t in single_support.items() if len(t) >= 2)
    res.census["n_known_single_tokens"] = len(known_tokens)

    # ---- 1. route every intervention name; provisional clusters keyed by dedup_key (combos by components)
    routed: dict[tuple[str, int], Keyed] = {}
    surfaces: dict[str, Counter] = defaultdict(Counter)  # provisional key → raw surface counts
    trials_by_key: dict[str, set[str]] = defaultdict(set)
    route_cache: dict[str, Keyed] = {}
    for iv in interventions:
        res.census["n_interventions_in"] += 1
        k = route_cache.get(iv.name_raw)
        if k is None:
            k = route_cache[iv.name_raw] = route(iv.name_raw, known_tokens)
        routed[(iv.nct_id, iv.intervention_no)] = k
        if k.key is None:
            res.gate_census[k.gate_reason or "unknown"] += 1
            continue
        for _part, g in k.dropped_parts:
            res.dropped_parts_census[g] += 1
        res.census[f"route_{k.route}"] += 1
        if k.is_combo:
            for ck, surf in zip(k.components, k.component_surfaces, strict=True):
                uf.add(ck)
                surfaces[ck][surf] += 1
                trials_by_key[ck].add(iv.nct_id)
        else:
            uf.add(k.key)
            surfaces[k.key][_trim_surface(iv.name_raw)] += 1
            trials_by_key[k.key].add(iv.nct_id)

    # ---- 2. otherNames → alias claims (alias_key → {provisional keys that claim it})
    claims: dict[str, set[str]] = defaultdict(set)
    alias_surfaces: dict[str, Counter] = defaultdict(Counter)
    alias_trials: dict[str, set[str]] = defaultdict(set)
    support: dict[tuple[str, str], set[str]] = defaultdict(
        set
    )  # (alias_key, claimant key) → asserting trials
    for (nct, no), names in other_names.items():
        k = routed.get((nct, no))
        if k is None or k.key is None:
            continue
        owners = k.components if k.is_combo else [k.key]
        if k.is_combo:
            continue  # an otherName on a combination intervention names the combo, not a component — skip
        for name in names:
            res.census["n_other_names_in"] += 1
            a = route(name)
            if a.key is None:
                res.other_name_gate_census[a.gate_reason or "unknown"] += 1
                continue
            if a.is_combo:
                res.other_name_gate_census["combo_shaped_other_name"] += 1
                continue
            for owner in owners:
                if a.key == owner:
                    continue
                claims[a.key].add(owner)
                alias_surfaces[a.key][_trim_surface(name)] += 1
                alias_trials[a.key].add(nct)
                support[(a.key, owner)].add(nct)

    # ---- 3. merge pass. An alias claimed by ONE root → merge/assign. Claimed by ≥2 roots → the DOMINANCE rule:
    # assign only when one claimant is asserted by ≥ DOMINANCE_MIN_TRIALS trials AND ≥ DOMINANCE_RATIO × every other
    # claimant (typos, regimen acronyms and qualified phrases each claim a brand in a trial or two; the real
    # asset claims it in hundreds). Everything else stays CONTESTED: logged, never applied. Every decision is
    # written to contested_aliases with its resolution so it can be audited.
    def roots_of(keys: set[str]) -> set[str]:
        return {uf.find(k) for k in keys}

    def support_by_root(alias: str) -> dict[str, int]:
        agg: dict[str, set[str]] = defaultdict(set)
        for owner in claims[alias]:
            agg[uf.find(owner)] |= support[(alias, owner)]
        return {r: len(t) for r, t in agg.items()}

    def dominant_root(alias: str) -> str | None:
        sup = sorted(support_by_root(alias).items(), key=lambda kv: (-kv[1], kv[0]))
        if len(sup) == 1:
            return sup[0][0]
        top, second = sup[0], sup[1]
        if top[1] >= DOMINANCE_MIN_TRIALS and top[1] >= DOMINANCE_RATIO * second[1]:
            return top[0]
        return None

    changed = True
    while changed:
        changed = False
        for alias in claims:
            r = roots_of(claims[alias])
            target = next(iter(r)) if len(r) == 1 else dominant_root(alias)
            if target and alias in uf.parent and uf.find(alias) != target:
                uf.union(alias, target)  # the alias is itself another asset's name → merge the two clusters
                changed = True

    # ---- 4. assign aliases; log contested and dominance decisions
    alias_owner: dict[str, str] = {}  # alias_key → root
    for alias, owners in claims.items():
        r = roots_of(owners)
        if alias in uf.parent:
            r.add(uf.find(alias))
        if len(r) == 1:
            alias_owner[alias] = next(iter(r))
            continue
        dom = dominant_root(alias)
        if dom is not None:
            alias_owner[alias] = dom
            res.contested.append((alias, sorted(r), len(alias_trials[alias]), f"dominance:{dom}"))
            res.census["n_alias_dominance_resolutions"] += 1
        else:
            res.contested.append((alias, sorted(r), len(alias_trials[alias]), "vetoed"))
            res.census["n_contested_aliases"] += 1

    # ---- 5. materialize clusters → assets
    members: dict[str, list[str]] = defaultdict(list)
    for k in list(uf.parent):
        members[uf.find(k)].append(k)

    def choose_canonical(keys: list[str]) -> tuple[str, str]:
        """(canonical_name, dedup_key): canonical = the most frequent NON-CODE surface across the cluster
        (code names only when nothing else exists); the cluster key = the member key that surface routes to,
        so the asset id reads like its name. Ties break by count, then shorter, then alpha — never insertion order."""
        surf_by_key: dict[str, Counter] = {k: surfaces.get(k, Counter()) for k in keys}
        best_key = max(keys, key=lambda k: (len(trials_by_key.get(k, ())), -len(k), k))
        candidates = [(n, c, k) for k, sc in surf_by_key.items() for n, c in sc.items()]
        non_code = [x for x in candidates if not is_code_name(x[0])]
        pool = non_code or candidates
        if not pool:
            return best_key, best_key
        # rank: non-code > not ALL-CAPS (brands are usually shouted) > surface count > trials on its key > shorter
        name, _, key = max(
            pool,
            key=lambda x: (not x[0].isupper(), x[1], len(trials_by_key.get(x[2], ())), -len(x[0]), x[0]),
        )
        if is_code_name(name):
            name = name.upper()
        return name, key

    root_to_asset: dict[str, str] = {}
    for root, keys in members.items():
        name, dk = choose_canonical(keys)
        asset_id = dk
        root_to_asset[root] = asset_id
        n_trials = len(set().union(*(trials_by_key.get(k, set()) for k in keys)))
        # brand display: an alias surface carrying ® / ™ in the raw registry text
        res.assets[asset_id] = {
            "canonical_name": name,
            "dedup_key": dk,
            "is_combo": False,
            "n_trials": n_trials,
        }
        for k in keys:
            top = surfaces[k].most_common(1)
            res.aliases.setdefault(k, (asset_id, top[0][0] if top else k, "name"))
    # contested log: translate union-find roots → asset ids (the audit table must name assets, not roots)
    res.contested = [
        (
            alias,
            sorted({root_to_asset.get(uf.find(k), k) for k in ids}),
            n,
            f"dominance:{root_to_asset.get(uf.find(reso[10:]), reso[10:])}"
            if reso.startswith("dominance:")
            else reso,
        )
        for alias, ids, n, reso in res.contested
    ]
    for alias, root in alias_owner.items():
        aid = root_to_asset[root]
        if alias in res.aliases and res.aliases[alias][0] != aid:
            res.census["n_alias_key_collisions"] += 1  # should not happen: uniqueness guard
            continue
        top = alias_surfaces[alias].most_common(1)
        res.aliases.setdefault(alias, (aid, top[0][0] if top else alias, "other_name"))

    # brand-in-parentheses display convention: "generic (BRAND)" when an ® alias is known and differs
    brand_by_asset: dict[str, str] = {}
    for (nct, no), names in other_names.items():
        for name in names:
            if "®" in name or "™" in name:
                k = routed.get((nct, no))
                if k and k.key and not k.is_combo:
                    aid = root_to_asset.get(uf.find(k.key))
                    if aid and aid not in brand_by_asset:
                        brand_by_asset[aid] = _trim_surface(name)
    for aid, brand in brand_by_asset.items():
        a = res.assets[aid]
        if brand.lower() != a["canonical_name"].lower():
            a["canonical_name"] = f"{a['canonical_name']} ({brand})"

    # ---- 6. combos: recompute keys from component roots; create combo assets + component edges
    combo_ids: dict[tuple[str, ...], str] = {}
    combo_trials: dict[str, set[str]] = defaultdict(set)
    seen_components: set[tuple[str, str]] = set()
    for (nct, no), k in routed.items():
        if k.key is None:
            continue
        if not k.is_combo:
            res.intervention_assets[(nct, no)] = [(root_to_asset[uf.find(k.key)], "name")]
            continue
        comp_ids = tuple(sorted({root_to_asset[uf.find(c)] for c in k.components}))
        if len(comp_ids) == 1:  # components merged into one asset (e.g. "X/X" under aliases)
            res.intervention_assets[(nct, no)] = [(comp_ids[0], "name")]
            continue
        cid = combo_ids.get(comp_ids)
        if cid is None:
            cid = "+".join(comp_ids)
            combo_ids[comp_ids] = cid
            res.assets[cid] = {
                "canonical_name": " + ".join(
                    res.assets[c]["canonical_name"].split(" (")[0] for c in comp_ids
                ),
                "dedup_key": cid,
                "is_combo": True,
                "n_trials": 0,
            }
            for c in comp_ids:
                if (cid, c) not in seen_components:
                    seen_components.add((cid, c))
                    res.components.append((cid, c))
        combo_trials[cid].add(nct)
        res.intervention_assets[(nct, no)] = [(cid, "name")] + [(c, "combo_component") for c in comp_ids]
    for cid, ts in combo_trials.items():
        res.assets[cid]["n_trials"] = len(ts)
        res.aliases.setdefault(cid, (cid, res.assets[cid]["canonical_name"], "name"))

    res.census["n_assets"] = sum(1 for a in res.assets.values() if not a["is_combo"])
    res.census["n_combo_assets"] = sum(1 for a in res.assets.values() if a["is_combo"])
    res.census["n_aliases"] = len(res.aliases)
    res.census["n_merged_via_other_names"] = sum(len(ks) - 1 for ks in members.values())
    res.census["n_interventions_keyed"] = len(res.intervention_assets)
    res.census["n_interventions_gated"] = sum(res.gate_census.values())
    return res

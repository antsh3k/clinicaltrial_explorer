import pytest

from ct_landscape.normalize.drug_names import clean, dedup_key, gate, is_code_name, route

# ---------------------------------------------------------------- gates


@pytest.mark.parametrize(
    "raw",
    [
        "Placebo",
        "placebo",
        "Placebos",
        "Placebo oral tablet",
        "Matching placebo",
        "Placebo for pembrolizumab",  # the WHOLE label dies — never leak the drug out of a comparator string
        "Placebo matching IMU-838",
        "Placebo: Negative control",
        "Nitrogen (placebo)",
        "Sham",
        "Vehicle",
        "Normal saline",
        "Standard of care",
        "Best supportive care",
        "No intervention",
        "Control",
        "Chemotherapy",
        "corticosteroids",
        "Statins",
        "TKIs",
        "Immunotherapy",
        "Checkpoint inhibitors",
        "Anti-PD-1",
        "Platinum-based",
        "Drug: Treatment C",
        "Treatment C",
        "Arm A",
        "Study drug",
        "Blood sample",
        "Dose escalation",
        "Drug: Benzodiazepine (listed out below)",
        "Fluoxetine placebo",
        "Noiiglutide Placebo",
        "Neoadjuvant chemotherapy",
        "Chemotherapy drug",
        "platinum doublet chemotherapy",
        "therapy",
        "Lymphodepleting chemotherapy",
        "N/A",
        "12",
        "ab",
    ],
)
def test_placebo_never_an_asset(raw):
    assert route(raw).key is None, raw


@pytest.mark.parametrize(
    "raw, expected_key",
    [
        (
            "pembrolizumab immunotherapy",
            "pembrolizumab",
        ),  # names a molecule beside a class word: survives, keyed to the molecule
        ("Human fibrinogen concentrate", "fibrinogen"),
        ("Pembrolizumab", "pembrolizumab"),
        ("antipyrine", "antipyrine"),  # class shape needs a separator
        ("Antithrombin III", "antithrombiniii"),
    ],
)
def test_gates_are_whole_label_only(raw, expected_key):
    assert dedup_key(raw) == expected_key


# ---------------------------------------------------------------- cleaning + keys


@pytest.mark.parametrize(
    "raw, key",
    [
        ("Drug: Pembrolizumab", "pembrolizumab"),
        ("Experimental: Pembrolizumab 200 mg IV Q3W", "pembrolizumab"),
        ("Active Comparator: Olaparib tablets", "olaparib"),
        ("Pembrolizumab (MK-3475)", "pembrolizumab"),
        ("Secukinumab (IL-17A inhibitor)", "secukinumab"),
        ("KEYTRUDA®", "keytruda"),
        ("Doxorubicin hydrochloride", "doxorubicin"),
        ("Gemcitabine Hydrochloride", "gemcitabine"),
        ("Vincristine sulfate", "vincristine"),
        ("Leucovorin calcium", "leucovorin"),
        ("Diclofenac sodium", "diclofenac"),
        ("lisdexamfetamine dimesylate chewable tablet", "lisdexamfetamine"),  # needs the fixed-point loop
        ("Metformin extended-release tablets 500 mg", "metformin"),
        ("Ketamine 0.75mg/kg", "ketamine"),
        ("Insulin glargine 100 units/mL", "insulinglargine"),
        ("Aripiprazole depot 25 or 50 mg", "aripiprazole"),
        ("Adalimumab prefilled syringe", "adalimumab"),
        ("Semaglutide once weekly", "semaglutide"),
        ("Ａｓｐｉｒｉｎ", "aspirin"),  # fullwidth
        ("Pembrolizumab or Pembrolizumab", "pembrolizumab"),  # "X or X" arm strings
        ("Pembrolizumab or matching placebo", "pembrolizumab"),
        ("sedation with propofol", "propofol"),  # 'sedation' gated, one survivor
        ("Cytarabine or Supportive Care", "cytarabine"),  # one survivor of an either/or
        ("oral baclofen + placebo", "baclofen"),
        ("Neoadjuvant nivolumab", "nivolumab"),
        ("pembrolizumab immunotherapy", "pembrolizumab"),
        ("High Dose Melphalan", "melphalan"),
        ("maintenance pemetrexed", "pemetrexed"),
        ("Pemetrexed after protocol amendment", "pemetrexed"),
        ("Single low dose cyclophosphamide", "cyclophosphamide"),
        ("erlotinib monotherapy", "erlotinib"),
        ("Gemcitabine alone", "gemcitabine"),
        ("low-dose dexamethasone", "dexamethasone"),
        ("Benmelstobart combined with chemotherapy", "benmelstobart"),
    ],
)
def test_cleaning_and_fixed_point_keys(raw, key):
    assert dedup_key(raw) == key


def test_either_or_of_two_molecules_is_not_an_asset():
    r = route("Empagliflozin or Dapagliflozin Pill")
    assert r.key is None and r.gate_reason == "either_or_arm"


@pytest.mark.parametrize(
    "raw, key",
    [
        ("Potassium chloride", "potassiumchloride"),  # electrolyte guard
        ("Magnesium sulfate", "magnesiumsulfate"),
        ("Sodium chloride", "sodiumchloride"),
        ("Lithium carbonate", "lithiumcarbonate"),
    ],
)
def test_electrolyte_guard(raw, key):
    assert dedup_key(raw) == key


@pytest.mark.parametrize(
    "a, b",
    [
        ("Epoetin alfa", "Epoetin beta"),
        ("Interferon beta-1a", "Interferon beta-1b"),
        ("Trastuzumab", "Trastuzumab-dkst"),  # biosimilar stays distinct from reference
        ("Adalimumab", "Adalimumab-atto"),
        ("Insulin glargine", "Insulin lispro"),
    ],
)
def test_biologic_isoforms_and_biosimilars_stay_distinct(a, b):
    ka, kb = dedup_key(a), dedup_key(b)
    assert ka and kb and ka != kb


def test_biologic_shape_keeps_greek_but_strips_dose_form():
    assert dedup_key("Epoetin alfa injection") == "epoetinalfa"
    assert dedup_key("Trastuzumab deruxtecan") == "trastuzumabderuxtecan"
    assert dedup_key("Enfortumab vedotin-ejfv") == "enfortumabvedotinejfv"


# ---------------------------------------------------------------- combos


@pytest.mark.parametrize(
    "raw, key, components",
    [
        ("Carbidopa/Levodopa", "carbidopa+levodopa", ["carbidopa", "levodopa"]),
        ("IBUPROFEN + CAFFEINE", "caffeine+ibuprofen", ["caffeine", "ibuprofen"]),
        ("Ledipasvir 90 mg/Sofosbuvir 400 mg", "ledipasvir+sofosbuvir", ["ledipasvir", "sofosbuvir"]),
        ("teriparatide and alendronate", "alendronate+teriparatide", ["alendronate", "teriparatide"]),
        ("Linperlisib in combination with CHOP", "chop+linperlisib", ["chop", "linperlisib"]),
        ("Levodopa/Carbidopa", "carbidopa+levodopa", ["carbidopa", "levodopa"]),  # order-independent
    ],
)
def test_combination_names_keep_components(raw, key, components):
    r = route(raw)
    assert r.is_combo and r.key == key and r.components == components


def test_combo_drops_placebo_part_and_keeps_named_agents():
    r = route("paracetamol + pregabalin + placebo")
    assert r.key == "paracetamol+pregabalin"  # the trailing placebo clause is stripped before splitting
    r = route("paracetamol + standard of care + pregabalin")
    assert r.key == "paracetamol+pregabalin"
    assert ("standard of care", "exact_noise") in r.dropped_parts
    # a label that STARTS with placebo dies whole — never leak a drug out of a comparator string
    assert route("placebo + paracetamol").key is None


def test_combo_with_class_partner_keeps_named_agent_only():
    r = route("Pembrolizumab + chemotherapy")
    assert r.key == "pembrolizumab" and not r.is_combo
    assert r.dropped_parts == [("chemotherapy", "exact_noise")]


def test_known_token_regimens_route_as_combos():
    known = frozenset({"lenalidomide", "dexamethasone", "cisplatin", "docetaxel", "insulin", "sodium"})
    r = route("Lenalidomide Dexamethasone", known)
    assert r.is_combo and r.key == "dexamethasone+lenalidomide" and r.route == "combo_regimen"
    assert route("Cisplatin Docetaxel", known).key == "cisplatin+docetaxel"
    assert (
        route("Lenalidomide Dexamethasone").key == "lenalidomidedexamethasone"
    )  # without a vocabulary: unchanged
    r = route(
        "dexamethasone lenalidomide dexamethasone", known
    )  # repeated token: keys and surfaces stay aligned
    assert r.components == ["dexamethasone", "lenalidomide"] and r.component_surfaces == [
        "dexamethasone",
        "lenalidomide",
    ]
    assert route("Insulin glargine", known).key == "insulinglargine"  # biologic-shaped names never split
    assert route("Sodium chloride", known).key == "sodiumchloride"  # electrolyte guard


def test_combo_where_everything_is_gated_dies():
    r = route("Placebo + Standard of care")
    assert r.key is None


# ---------------------------------------------------------------- misc


@pytest.mark.parametrize(
    "s, expected",
    [
        ("MK-3475", True),
        ("ABBV-181", True),
        ("PF-04965842", True),
        ("BAY1747846", True),
        ("Pembrolizumab", False),
        ("5-FU", False),
    ],
)
def test_code_shape(s, expected):
    assert is_code_name(s) is expected


def test_clean_is_lowercase_and_trimmed():
    assert clean("  Drug:  Pembrolizumab  ") == "pembrolizumab"


def test_gate_names_every_reason():
    assert gate("placebo") == "placebo_sham_prefix"
    assert gate("standard of care") == "exact_noise"
    assert gate("dose escalation cohort") == "metadata_cue"
    assert gate("anti-pd-1") == "regex_noise"
    assert gate("pembrolizumab") is None

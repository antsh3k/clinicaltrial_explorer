# LLM-tier pilot review

## Statistics

```json
{
 "n_settled": 300,
 "outcomes": {
  "ok": 300,
  "invalid": 1
 },
 "abstain_rate": 0.104,
 "self_inconsistent": 0,
 "basis": {
  "well_known_drug": 269,
  "insufficient": 30
 },
 "confidence": {
  "high": 268,
  "low": 30,
  "medium": 1
 },
 "modality": {
  "small_molecule": 154,
  "adc": 8,
  "mab": 28,
  "vaccine": 33,
  "protein": 28,
  "peptide": 9,
  "unknown": 30,
  "cell_therapy": 2,
  "radiopharm": 1,
  "other": 6
 },
 "targets_unvalidated_rate": 0.248,
 "tokens_in_per_asset": 822,
 "tokens_out_per_asset": 110,
 "cache_read_per_asset": 0,
 "cost_usd": 0.206,
 "cost_per_asset_usd": 0.00069
}
```

## Hand-check sample (30 of 300, seed 1)

Mark each row ✓ / ✗ / ? in the `verdict` column. Context = aliases + up to 3 trial titles the model saw.

| # | asset (aliases) | trials seen | LLM: known / basis / conf | modality · action · targets · moa_class | abstain | verdict |
|---|---|---|---|---|---|---|
| 1 | **daraxonrasib** (RMC-0706236, RMC-6236) | Study of Daraxonrasib (RMC-6236) in Patients With RAS Mutated NSCLC (R / Study of Daraxonrasib and Daraxonrasib + GnP as First-line Treatment i / Study of Daraxonrasib (RMC-6236) in Patients With Resected Pancreatic  | yes / well_known_drug / high | small_molecule · inhibitor · KRAS · KRAS inhibitor | False |  |
| 2 | **VIMOVO (VIMOVOTM)** (VIMOVO, VIMOVOTM, PN400) | Efficacy Study of PN400 (VIMOVO) Twice Daily and Celebrex Once Daily i / Efficacy Study of PN400 (VIMOVO) Twice Daily and Celebrex Once Daily i / Evaluating PN 400 (VIMOVO) in Reducing Gastric Ulcers Compared to Non- | yes / well_known_drug / high | small_molecule · inhibitor · PTGS1, PTGS2 · NSAID + PPI combination | False |  |
| 3 | **BGM0504 Injection single-dose pen** (BGM0504) | A Study of BGM0504 in Early T2DM With Obesity / A Study of BGM0504 in Participants With Type 2 Diabetes in Indonesia / A Study of BGM0504 in Participants With Type 2 Diabetes in Indonesia | no / insufficient / low | unknown · unknown ·  · — | True |  |
| 4 | **inhaled gsk573719** (GSK573719) | A 52-Week, Multicenter, Randomized, Double-Blind, Parallel-Group, Plac / A 52-Week, Multicenter, Randomized, Double-Blind, Parallel-Group, Plac / An Exercise Endurance Study to Evaluate the Effects of Treatment of Ch | yes / well_known_drug / high | small_molecule · antagonist · CHRM3, ADRB2 · dual bronchodilator (M3 antagonist / beta-2 agonist) | False |  |
| 5 | **Clofazimine (ACZONE)** (Vulgarex, ACZONE Gel, ACZONETM Gel, Clofazimine, Dapsone, DDS, lamprene, rifampc) | Randomized, Triple-Blind, Vehicle-Controlled Trial of Topical Dapsone  / Efficacy and Safety of add-on Dapsone Versus add-on Methotrexate in Pa / A Study of an Oral Short-course Regimen Including Bedaquiline for the  | yes / well_known_drug / high | small_molecule · unknown ·  · — | True |  |
| 6 | **Sodium Chloride** (ACT-code: B05XA03, hyeprtonic sodium chloride, Isotone saline, Istone saline, Na) | The Impact of Dietary Salt on the Severity of Eczema / The Impact of Salt Intake on Sodium in the Skin and Inflammatory Skin  / Efficacy Of Combined Nebulized Hypertonic Saline and Chest Percussion  | yes / well_known_drug / high | small_molecule · other ·  · — | False |  |
| 7 | **Rescue** () | Safety Study of MK-3641 and MK-7243 Co-administered in Adult Participa / A Clinical Study of Ifinatamab Deruxtecan (I-DXd) in People With Metas / A Study to Evaluate Zilovertamab Vedotin (MK-2140) Combination With Ri | no / insufficient / low | unknown · unknown ·  · — | True |  |
| 8 | **Sacituzumab tirumotecan** (MK-2870, sac-TMT, SKB264, SKB264, MK-2870) | A Clinical Study of Sacituzumab Tirumotecan (Sac-TMT, MK-2870) in Peop / A Clinical Study of Sacituzumab Tirumotecan (MK-2870) in Combination W / Sacituzumab Tirumotecan (MK-2870) in Combination With Pembrolizumab Ve | yes / well_known_drug / high | adc · other · TROP-2 · TROP-2 directed ADC | False |  |
| 9 | **NGR-hTNF** (sqm iv) | NGR015: Study in Second Line for Patient With Advanced Malignant Pleur / A Multicenter Open-label Phase II Study of Cemiplimab Plus Chemotherap / A Multicenter Open-label Phase II Study of Cemiplimab Plus Chemotherap | yes / well_known_drug / high | protein · agonist · TNF · TNF agonist | False |  |
| 10 | **Faster-acting insulin aspart** (NN1218) | Research Study Comparing a New Medicine "Fast-acting Insulin Aspart" t / Efficacy and Safety of Continuous Subcutaneous Insulin Infusion of Fas / Efficacy and Safety of FIAsp in a Basal-bolus Regimen Versus Basal Ins | yes / well_known_drug / high | protein · agonist · insulin receptor · rapid-acting insulin analog | False |  |
| 11 | **Canakinumab (Ilaris)** (Tolazamise, Tolbutamise, Tolinase, Ultralente, Velosulin, ACZ885, Canakinumab, C) | COVID-19 VaccinE Response in Rheumatology Patients / Study of Efficacy and Safety of Pembrolizumab Plus Platinum-based Doub / Using a Contact Dermatitis Model With Biologic Medications to Study Sk | yes / well_known_drug / high | mab · antagonist · IL1B · IL-1β antagonist | False |  |
| 12 | **shr-a1811** (Trastuzumab Rezetecan, T-DXh) | Real-world Study of Pyrotinib-containing Regimens of Advanced HER2-pos / Trastuzumab Rezetecan (T-DXh) in HER2+ Breast Cancer With Non-pCR Afte / An Open-Label, Randomized Phase III Study of Trastuzumab Rezetecan Wit | yes / well_known_drug / high | adc · other · ERBB2 · HER2-directed antibody-drug conjugate | False |  |
| 13 | **Amoxicillin (Clamoxyl)** (Trimox, Two-dose group, dose 1 of 2, Two-dose group, dose 2 of 2, uricosuric, Zi) | Congenital Syphilis Treatment Trial (CONSISTENT) in Neonates / Plasma Concentrations of Amoxicillin Administered in High-doses During / Optimizing H. Pylori Eradication Regimen Under Intensified Acid Suppre | yes / well_known_drug / high | small_molecule · inhibitor · PBP · Beta-lactam antibiotic | False |  |
| 14 | **Ofatumumab (Arzerra)** (Arzera, Arzerra, Azerra, Chlorambucil, GSK1841157, HuMax-CD20, HuMax-CD20, 2F2, ) | A Multicenter Study of Continued Current Therapy vs Transition to Ofat / Study to Assess the Effect of Ofatumumab in Treatment Naïve, Very Earl / Study of Efficacy and Safety of Ofatumumab in Relapsing Multiple Scler | yes / well_known_drug / high | mab · antagonist · CD20 · CD20 antagonist | False |  |
| 15 | **Prevnar 13 (Prevenar)** (20-valent pneumococcal conjugate vaccine, pneumococcal 13-valent conjugate vacci) | Induction of Cross-protective Antibodies for Serogroup 33 by Pneumococ / Vaccine Responses in Patients With B Cell Malignancies / Influence of Methotrexate Discontinuation on Immunogenicity After PCV- | yes / well_known_drug / high | vaccine · other ·  · pneumococcal conjugate vaccine | False |  |
| 16 | **abiraterone (Zytiga)** (Yonsa, Zytiga, 16-dien-3beta-ol, 17-androsta-5, abiraterone, CB 7598, JNJ-212082) | Cognitive Effects of Androgen Receptor Directed Therapies for Advanced / Determining the Effect of Abiraterone on the Metabolism of Oxycodone i / A Study of Abiraterone Acetate in Metastatic Castration-Resistant Pros | yes / well_known_drug / high | small_molecule · inhibitor · CYP17A1 · CYP17A1 inhibitor | False |  |
| 17 | **REGN7508** (cenvacibart) | Stroke and Systemic Embolism Prevention in Adult Participants With Atr / REGN7508 in Adult Participants for Prevention of Cancer-Associated Thr / Reducing Adverse Vascular Outcomes With Factor XI Inhibition in Adult  | yes / well_known_drug / high | mab · antagonist · TF, Tissue Factor · Tissue Factor antagonist | False |  |
| 18 | **hib (Hiberix)** (Vaxem Hib, Haemophilus influenzae type b vaccine, hib) | A Study to Evaluate Immunogenicity and Safety of GlaxoSmithKline (GSK) / Apnea in Hospitalized Preterm Infants Following the Administration of  / Primary and Booster Vaccination Study With Pneumococcal Vaccine GSK102 | yes / well_known_drug / high | vaccine · other ·  · Haemophilus influenzae type b vaccine | False |  |
| 19 | **folfox (Eloxatin/Leucovorin/5-FU)** (Total mesorectal excision -> of FOLFOX, fluorouracil, oxaliplatin, leucovorin, F) | Paclitaxel, Ramucirumab and Tislelizumab Switch Maintenance in Advance / A Study to Evaluate the Safety and Efficacy of Pumitamig in Combinatio / A Study to Evaluate the Safety and Efficacy of Pumitamig in Combinatio | yes / well_known_drug / high | small_molecule · inhibitor · TYMS, DHFR · Combination chemotherapy (5-FU/leucovorin/oxaliplatin) | False |  |
| 20 | **Certolizumab Pegol (Cimzia)** (UCB product: Certolizumab Pegol, Xyloneural, Brand Name: Cimzia, CDP870, Certoli) | A Study to Assess the Effects of Certolizumab Pegol on the Reduction o / IL-7 and IL-7R Expression in RA Patients With Active vs. Inactive Dise / Dosing Flexibility Study in Patients With Rheumatoid Arthritis | yes / well_known_drug / high | mab · antagonist · TNF, TNF-alpha · TNF-alpha antagonist | False |  |
| 21 | **Laquinimod** (TV-5600, ABR-215062, LAQ eye drops) | BRAVO Study: Laquinimod Double-blind Placebo-controlled Study in Parti / A Study to Evaluate the Long-term Safety, Tolerability and Effect of D / A Study To Evaluate the Long-Term Safety, Tolerability and Effect on D | yes / well_known_drug / high | small_molecule · agonist · TLR7 · TLR7 agonist | False |  |
| 22 | **anrikefon** (HSK21542) | Anrikefon vs Nalfurafine for Sleep Quality in Hemodialysis Patients Wi / Anrikefon-based Patient-controlled Intravenous Analgesia Following Lap / Anrikefon-based Patient-controlled Intravenous Analgesia Following Lap | no / insufficient / low | unknown · unknown ·  · — | True |  |
| 23 | **Alprazolam (XANAX XR)** (Verucerfont, Xanax XR, Xanax Pfizer, Zamoprax, Zamoprax GlaxoSmithKline Mexico S) | Oral vs IV Sedation for Cataract Surgery in Older Adults / An Extension Test of Whether to Use Oral Anti-anxiety Drugs (Alprazola / Detoxification From the Lipid Tract | yes / well_known_drug / high | small_molecule · agonist · GABRA1 · GABA-A receptor agonist | False |  |
| 24 | **ALXN2050 MR Prototype** (ACH-0145228: Immediate Release, ACH-5228, ALXN2050) | Study of ALXN2050 in Proliferative Lupus Nephritis (LN) or Immunoglobu / Study of ALXN2050 in Adult Participants With Generalized Myasthenia Gr / Study of the Oral Factor D (FD) Inhibitor ALXN2050 in PNH Patients as  | yes / well_known_drug / high | small_molecule · inhibitor · CFD · Factor D inhibitor | False |  |
| 25 | **trifluridine (LONSURF)** (trifluridine, Lonsurf, TAS-102, tipiracil) | A Study to Access Intravenous (IV) Telisotuzumab Adizutecan in Combina / Neoadjuvant Radiotherapy for Rectal Adenocarcinoma With Capecitabine V / A Clinical Study to Evaluate Injection TQB2102 for the Treatment of Pa | yes / well_known_drug / high | small_molecule · inhibitor · TYMS, RRM1 · thymidylate synthase inhibitor | False |  |
| 26 | **adjuvant** () | Moxifloxacin in Adjuvant Treatment of Patients With Operable Breast Ca / Concurrent Chemoradiotherapy With or Without Metronomic Capecitabine i / Safety and Immunogenicity of CHIKV VLP Vaccine PXVX0317 in Adults ≥65  | no / insufficient / low | unknown · unknown ·  · — | True |  |
| 27 | **niacin (Niaspan FCT)** (ABT-919, Control, Vitamin B3, niacin, Niaspan FCT, Slo-niacin) | Evaluate Carotid Artery Plaque Composition by Magnetic Resonance Imagi / The Efficacy of Niacin on Hyperphosphatemia in Patients Undergoing Hae / Characterization of High Density Lipoprotein (HDL) in Type 2 Diabetes  | yes / well_known_drug / high | small_molecule · other ·  · Vitamin B3 / Nicotinic acid | False |  |
| 28 | **Fluarix** (GlaxoSmithKline Biologicals' licensed influenza vaccine) | The Impact of Imprinting and Repeated Influenza Vaccination on Adaptiv / Response of Older Adults to Influenza Vaccination With Regard to Cytom / Immunogenicity, Safety of GSKs Tdap Vaccine Boostrix When Coadminister | yes / well_known_drug / high | vaccine · other ·  · influenza vaccine | False |  |
| 29 | **PfSPZ Challenge** (Aseptic, cryopreserved P. falciparum sporozoites, aseptic, purified, cryopreserv) | Comparing Safety and Protective Efficacy of Vaccine Candidate PfSPZ-CV / Controlled Human Malaria Infection Transmission Model - Phase A / Sanaria PfSPZ Challenge With Pyrimethamine or Chloroquine Chemoprophyl | yes / well_known_drug / high | vaccine · other ·  · malaria vaccine (live attenuated sporozoite) | False |  |
| 30 | **Tirofiban (KENGREXAL)** (trade name: Xinweining; Wuhan Grand Pharmaceutical Group Co., LTD., Xinweining, ) | Tirofiban Combined With Aspirin in Moderate Ischemic Stroke / The Bridging Antiplatelet Therapy With Cangrelor 2 Study / ATILA Project: Aspirin Versus Tirofiban in Endovascular Treatment for  | yes / well_known_drug / high | small_molecule · inhibitor · ITGA2B, ITGB3 · Glycoprotein IIb/IIIa inhibitor | False |  |

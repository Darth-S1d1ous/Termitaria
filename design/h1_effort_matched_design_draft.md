# DRAFT (not yet in KG) — H1-first effort-matched field programme

Status: KG unreachable at time of writing. This file is the working draft pending
successful KG write.

## Sequencing
1. H1 (field/design testable) — primary.
2. H2 (caste variability) — opportunistic add-on; needs gynes, which H1 sampling may yield.
3. H3 (LHF duct-organ architecture) — opportunistic add-on; needs larvae in ethanol and
   micro-CT access. PREMISE UNVERIFIED outside L. japonica (Masuko 1989, 1990).

## H1 — predictions / falsification
P1: Effort-matched yield (species per unit standardised effort) at a "species-poor"
   region is statistically indistinguishable from a Mediterranean reference site.
P2: Residual species counts are explained by sampling-effort covariates, not region.
P3: Published richness asymmetry disappears after effort standardisation.
Falsification: if effort-matched sampling reproduces the published asymmetry with
   effort covariates in the model, H1 is refuted.

## Effort-matching variables (must be logged per sample)
- Winkler extractor: number of sacks, kg litter per sack, extraction duration (days),
  sieve mesh size.
- Soil cores: count, diameter, depth (cm), total volume (cm3).
- Plot size at each site (5x5 m, 10x10 m).
- Number of samples per site (target >= 20 independent sample units per site).
- Transect-level: distance walked, litter depth measured at each sample point.
- Observer/collector identity and per-sample handling time.

## Response variables
- Worker-based morphospecies richness (counts by caste).
- Male-based taxa scored separately and flagged as unlinked (14 species known only
  from males per Qian et al. 2024).
- Occupancy: detection/non-detection matrix per sample unit per morphospecies.
- Assemblage: species incidence, singleton rate, and per-caste representation to expose
  the taxonomic impediment.

## Specimen handling and databasing
- Point-preserved in 96% ethanol; one or two specimens per morphospecies set aside
  undissected for micro-CT (non-destructive route per Eur. J. Taxon. 2867).
- Assigned specimen IDs, link to sample unit ID, with a dataset field for caste.
- Barcode a subset for the total-evidence framework (Griebenow 2026) where budget allows.

## Statistical model
- Multi-species occupancy / N-mixture model with sampling-effort covariates.
- Hierarchical richness model: species richness ~ site + effort covariates + (1|sample unit).
- Effort covariates modelled as measured, not assumed.
- Explicit comparison against "raw count" model to show how much effort bias inflates
  region effects.

## Confounds
- Biotope type (allocated as strata, effort matched within stratum).
- Seasonality of brood and colony migration (multi-visit design; at minimum one late-dry
  and one wet/early-wet pass).
- Colonial structure — nests are whole sampling units, so colony-level non-independence
  is nested.
- Taxonomic impediment — worker and male determinations tracked as separate data streams.

## Budget / timeline skeleton (skeleton only)
- Pilot: one paired site, >= 20 sample units per site, effort standardised; 1 field season.
- Full: 4-6 sites across two regions, 3 passes, fixed effort.
- Contingency line for micro-CT time and barcoding.

## Minimum viable pilot
One "species-poor" site paired with one Mediterranean reference site, identical effort,
20 Winkler sacks + 20 soil cores per site per pass, 2 passes. Go/no-go decision on
H1 depends on whether effort covariates explain the site contrast in the occupancy model.

## Explicit caveat for H3
No published record confirms larval hemolymph feeding (LHF) or a larval duct organ outside
L. japonica (Masuko 1989, 1990). H3's premise is unverified; the programme must treat LHF
detection in any non-japonica material as a new, self-standing record requiring its own
vouchers and verification.

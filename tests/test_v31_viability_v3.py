# -*- coding: utf-8 -*-
"""V31 — verrous sur la MATÉRIALITÉ (V3) et sa cohérence avec V1 (R2).

Écart assumé à la lettre de la SPEC §4.4, verrouillé ici.

La SPEC définissait le P&L indicatif à ``p = 0,5`` et testait V3 sur sa VALEUR
ABSOLUE. Deux conséquences, toutes deux incompatibles avec R2 :

  1. INCOHÉRENCE AVEC V1. V1 borne l'avantage EXIGÉ au-dessus du point neutre
     ``p0 = d/(u+d)``. Évaluer l'espérance à ``p = 0,5`` revient à supposer une
     probabilité qui n'est ni le null, ni l'avantage revendiqué : les deux
     gates raisonnent alors dans deux régimes de probabilité différents.
  2. LA MATÉRIALITÉ RÉCOMPENSAIT LA PERTE. Sur un plan très asymétrique
     (u petit, d grand), l'espérance à ``p = 0,5`` est fortement NÉGATIVE ; sa
     valeur absolue franchissait allègrement le seuil. Plus le trade était
     mauvais, plus il paraissait « matériel ».

V31 évalue donc l'espérance à l'avantage REVENDIQUÉ, seul point cohérent avec
V1 : ``p = p0 + Δ_revendicable``. Sous la marche sans dérive à deux barrières,
``p0·u − (1 − p0)·d = 0`` exactement, d'où la forme close

        pnl% = Δ_revendicable × (u + d) − c

et V3 teste une valeur SIGNÉE. La cohérence V1 <-> V3 devient structurelle :
``pnl% > 0`` équivaut exactement à ``Δ_exigé < Δ_revendicable``, c'est-à-dire à
V1 franchi. Aucun trade dont V1 échoue ne peut afficher une espérance positive.
"""
from __future__ import annotations

import pytest

from src.core import params, viability
from src.core.viability import Verdict

BASE = {
    "fee_rate": 0.001,
    "liquidity_bands": [{"min_daily_volume_usd": 0, "spread_pct": 0.02,
                         "slippage_pct": 0.02}],
    "delta_claimable": 0.05,
    "p_target_max": 0.30,
    "p_stop_max": 0.20,
    "materiality_reference": "monthly_budget",
    "k3": 0.01,
    "monthly_budget": 500.0,
    "ticket_min": 40.0,
}

# Coût aller-retour induit : 2 × (0,1 + 0,01 + 0,02) = 0,26 point.
COST_PCT = 0.26


@pytest.fixture
def cfg():
    params.reset_cache()
    params._cache = dict(BASE)
    yield
    params.reset_cache()


def _eval(u, d, notional=500.0, sigma=8.0):
    return viability.evaluate(
        upside_pct=u, downside_pct=d, sigma_h_pct=sigma,
        sigma_degraded=False, notional_usd=notional, tranches=1,
        daily_volume_usd=5e8, ptf_value_usd=10000.0)


# ══════════════════════════════════════════════════════════════════════════
# Forme close et cohérence V1 <-> V3
# ══════════════════════════════════════════════════════════════════════════

def test_expected_pnl_follows_the_closed_form_at_the_claimed_edge(cfg):
    u, d = 12.0, 16.0
    v = _eval(u, d, notional=500.0)
    expected_pct = BASE["delta_claimable"] * (u + d) - COST_PCT
    assert v.expected_pnl_usd_net == pytest.approx(
        expected_pct / 100.0 * 500.0, abs=1e-6)


def test_V1_passing_is_exactly_equivalent_to_a_non_negative_expectation(cfg):
    """Verrou de cohérence R2 : les deux gates partagent le même régime.

    L'équivalence vaut sur le DOMAINE ÉNONÇABLE (p0 + Δ <= 1). Hors de ce
    domaine, V1 rejette pour une raison distincte — la revendication est
    impossible — et l'espérance n'a plus de sens à comparer.
    """
    for u, d in ((12.0, 16.0), (3.0, 2.0), (30.0, 5.0), (1.0, 1.0),
                 (20.0, 20.0), (2.6, 2.6), (5.0, 3.0), (0.9, 0.9)):
        v = _eval(u, d)
        assert (v.p_null + BASE["delta_claimable"]) <= 1.0, (u, d)
        v1_passed = "V1" not in v.failed_conditions
        non_negative = v.expected_pnl_usd_net >= 0
        assert v1_passed == non_negative, (u, d, v.delta_required,
                                           v.expected_pnl_usd_net)


def test_an_unstatable_claimed_edge_is_rejected_by_V1_on_its_own(cfg):
    """p0 déjà proche de 1 : revendiquer un avantage de plus est impossible.

    La forme close en tirerait une espérance confortablement positive. Le rejet
    ne doit dépendre d'AUCUN autre gate — V2 pourrait l'attraper ici, mais s'y
    fier serait un accident, pas une protection.
    """
    v = _eval(u=0.5, d=40.0, notional=500.0)
    assert v.p_null > 0.95
    assert "V1" in v.failed_conditions
    assert any("non énonçable" in n for n in v.notes)
    # Et l'espérance rapportée est bornée : jamais l'illusion de la forme close.
    assert v.expected_pnl_usd_net < BASE["delta_claimable"] * (0.5 + 40.0) \
        / 100.0 * 500.0


def test_delta_required_is_the_cost_over_the_total_amplitude(cfg):
    u, d = 12.0, 16.0
    v = _eval(u, d)
    assert v.delta_required == pytest.approx(COST_PCT / (u + d), abs=1e-4)
    # Et p* − p0 vaut bien cet écart.
    assert (v.p_breakeven - v.p_null) == pytest.approx(v.delta_required,
                                                       abs=1e-3)


# ══════════════════════════════════════════════════════════════════════════
# La régression que l'écart supprime
# ══════════════════════════════════════════════════════════════════════════

def test_a_negative_expectation_plan_can_never_pass_V3(cfg):
    """L'ancienne V3, en VALEUR ABSOLUE, laissait passer une perte franche."""
    v = _eval(u=1.0, d=1.0, notional=500.0)
    assert v.verdict is Verdict.NON_VIABLE
    assert {"V1", "V3"} <= set(v.failed_conditions)
    assert v.expected_pnl_usd_net < 0


def test_size_never_rescues_a_structurally_unprofitable_plan(cfg):
    """Un plan à espérance négative reste négatif à toute taille (I14).

    Le sizing est une ENTRÉE de la viabilité, jamais une variable d'ajustement :
    aucun notional ne peut rendre matériel un plan dont l'espérance est
    négative, puisque le signe ne dépend pas de la taille.
    """
    for notional in (40.0, 200.0, 500.0, 5000.0, 50000.0):
        v = _eval(u=1.0, d=1.0, notional=notional)
        assert v.expected_pnl_usd_net < 0
        assert "V3" in v.failed_conditions


def test_the_v30_eth_trade_is_rejected_on_several_grounds(cfg):
    """« Acheter ETH ~2 000 $, cible 2 015-2 020 » : la régression fondatrice."""
    entry, target, stop = 2000.0, 2017.5, 1960.0
    u = (target - entry) / entry * 100.0          # +0,875 %
    d = (entry - stop) / entry * 100.0            # −2,0 %
    v = _eval(u, d, notional=500.0, sigma=15.0)
    assert v.verdict is Verdict.NON_VIABLE
    # Il échoue sur l'avantage exigé, sur le bruit ET sur la matérialité.
    assert {"V1", "V2", "V3"} <= set(v.failed_conditions)


# ══════════════════════════════════════════════════════════════════════════
# NON_EVALUABLE n'est jamais assimilé à VIABLE
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("missing", sorted(BASE))
def test_any_missing_business_parameter_yields_non_evaluable(missing):
    params.reset_cache()
    params._cache = {k: v for k, v in BASE.items() if k != missing}
    v = _eval(12.0, 16.0)
    assert v.verdict is Verdict.NON_EVALUABLE
    assert v.is_viable is False
    assert v.missing_inputs
    params.reset_cache()


def test_a_non_evaluable_verdict_carries_no_economic_number(cfg):
    params.reset_cache()
    params._cache = {}
    v = _eval(12.0, 16.0)
    assert v.verdict is Verdict.NON_EVALUABLE
    # Rien n'est chiffré : on ne peut pas prétendre mesurer sans référentiel.
    assert v.expected_pnl_usd_net is None and v.delta_required is None
    params.reset_cache()

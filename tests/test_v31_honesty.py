# -*- coding: utf-8 -*-
"""V31 — HONNÊTETÉ DU SILENCE (SPEC §4.5).

Quand le système n'émet rien, il doit dire POURQUOI, et la raison doit être
vraie. Trois situations sont rigoureusement distinctes :

  NON_VIABLE     un plan a été construit et chiffré, puis écarté ;
  NON_EVALUABLE  le verdict n'a pas pu être rendu (entrée manquante) ;
  NON ÉVALUÉ     le plan n'a même pas été construit (horizon désactivé, prix
                 absent, contrat impossible).

La v30 servait « rien ne vaut la peine » dans les trois cas. Un matin sans
réseau annonçait donc sereinement qu'aucune opportunité ne méritait d'être
saisie — une affirmation qu'aucun calcul ne soutenait.
"""
from __future__ import annotations

import math

import pytest

from src.core import params
from src.core.book import Direction, RecommendationBook
from src.core.volatility import DailyBar
from src.pipeline import runs

FULL_PARAMS = {
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

_TECH = {"signals": [{"category": "technical_struct", "weight": 4}]}
_NO_SIGNAL = {"signals": []}


def _series(n=120, amp=24.0):
    return [100.0 + amp * math.sin(i / 11.0) for i in range(n)]


def _emittable_series():
    """Canal 76-124 terminé par un glissement vers 90.

    Le prix final reste AU-DESSUS du plancher du canal : une invalidation
    existe donc sous l'entrée, au-delà du plancher de bruit. Sans cela, V2
    rejette et l'on ne testerait plus la ré-émission.
    """
    base = [100.0 + 24.0 * math.sin(i / 11.0) for i in range(87)]
    start, end, steps = base[-1], 90.0, 25
    return base + [start + (end - start) * (k + 1) / steps
                   for k in range(steps)]


def _bars(n=40, base=100.0):
    return [DailyBar(f"d{i}", base, base * 1.012, base * 0.988, base)
            for i in range(n)]


def _ctx(tmp_path):
    from src.core import runlog
    summary = runlog.new_run("morning")
    book = RecommendationBook(run_kind="morning", run_id=summary.run_id,
                              state_dir=tmp_path / "bk")
    return runs.RunContext(kind="morning", summary=summary, book=book)


def _spec(asset, **over):
    base = {"asset": asset, "direction": Direction.LONG_INCREASE,
            "is_core": False, "tier": 1, "signal_scoring": _TECH,
            "price": _series()[-1], "closes": _series(),
            "daily_bars": _bars(), "ptf_value_usd": 9450.0,
            "weight_pct": 4.76, "position_value_usd": 242.0,
            "daily_volume_usd": 5e8}
    base.update(over)
    return base


@pytest.fixture
def full():
    params.reset_cache()
    params._cache = dict(FULL_PARAMS)
    yield
    params.reset_cache()


# ══════════════════════════════════════════════════════════════════════════
# NON ÉVALUÉ — le cas que la v30 travestissait
# ══════════════════════════════════════════════════════════════════════════

def test_no_price_never_claims_that_nothing_was_worth_doing(tmp_path, full):
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [_spec("RENDER", price=None),
                                   _spec("FET", price=None)])
    msg = runs.nothing_to_do_reason(ctx)
    assert "prix spot indisponible" in msg
    assert "n'ont pas été évaluées" in msg
    # L'affirmation interdite : aucun calcul ne la soutient.
    assert "ne franchit les conditions de viabilité" not in msg


def test_insufficient_depth_for_the_long_horizon_is_reported_as_such(tmp_path,
                                                                     full):
    """Un cœur sans historique long : NON_EVALUABLE chiffré, pas un refus sec."""
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [_spec("BTC", is_core=True),
                                   _spec("ETH", is_core=True)])
    msg = runs.nothing_to_do_reason(ctx)
    assert "history_depth" in msg
    assert "on ne peut pas trancher" in msg
    assert "ne franchit les conditions de viabilité" not in msg


def test_identical_motives_are_grouped_not_repeated(tmp_path, full):
    """Vingt actifs au même motif ne produisent pas vingt fois la même phrase."""
    ctx = _ctx(tmp_path)
    assets = [f"DUST{i}" for i in range(20)]
    runs.evaluate_candidates(ctx, [_spec(a, tier=4) for a in assets])
    msg = runs.nothing_to_do_reason(ctx)
    assert "20 actifs" in msg
    for a in assets:
        assert a not in msg, "le motif est agrégé, pas listé actif par actif"


# ══════════════════════════════════════════════════════════════════════════
# NON_EVALUABLE — on ne sait pas, on ne conclut pas
# ══════════════════════════════════════════════════════════════════════════

def test_missing_business_parameters_say_we_cannot_decide(tmp_path):
    params.reset_cache()
    params._cache = {}
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [_spec("RENDER")])
    msg = runs.nothing_to_do_reason(ctx)
    assert "évaluable" in msg and "fee_rate" in msg
    assert "on ne peut pas trancher" in msg
    assert "rien ne vaut la peine" in msg  # cité pour être explicitement nié
    params.reset_cache()


def test_insufficient_history_is_not_a_verdict_on_opportunity(tmp_path, full):
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [_spec("RENDER", closes=_series(n=40))])
    msg = runs.nothing_to_do_reason(ctx)
    assert "history_depth" in msg
    assert "ne franchit les conditions de viabilité" not in msg


# ══════════════════════════════════════════════════════════════════════════
# NON_VIABLE — la seule situation où « rien ne vaut la peine » est vrai
# ══════════════════════════════════════════════════════════════════════════

def test_an_actually_evaluated_and_rejected_plan_may_say_so(tmp_path, full):
    ctx = _ctx(tmp_path)
    # Canal plat : les niveaux existent mais ne sortent pas du bruit.
    flat = [100.0 + 0.01 * i for i in range(120)]
    runs.evaluate_candidates(ctx, [_spec("FLAT", closes=flat, price=flat[-1])])
    assert any(c.verdict and c.verdict.failed_conditions
               for c in ctx.candidates)
    msg = runs.nothing_to_do_reason(ctx)
    assert "aucun geste évalué ne franchit les conditions de viabilité" in msg
    assert "S'abstenir est aussi une décision" in msg
    assert "Motifs :" in msg


def test_an_empty_universe_is_stated_plainly(tmp_path, full):
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [])
    assert runs.nothing_to_do_reason(ctx) == "Aucun actif à évaluer ce matin."


# ══════════════════════════════════════════════════════════════════════════
# Ré-émission bloquée — il y AVAIT un geste
# ══════════════════════════════════════════════════════════════════════════

def test_a_blocked_reemission_is_not_presented_as_an_absence_of_opportunity(
        tmp_path):
    """Budget élargi À DESSEIN : sinon c'est l'épuisement du budget mensuel qui
    arrête le second geste, et la contrainte de ré-émission n'est jamais
    atteinte. Le test viserait alors autre chose que son intitulé."""
    from src.core.book import State

    params.reset_cache()
    params._cache = dict(FULL_PARAMS, monthly_budget=5000.0, k3=0.001)

    series = _emittable_series()
    spec = _spec("RENDER", closes=series, price=series[-1])

    # 1er run : un contrat naît, puis il est invalidé.
    first = _ctx(tmp_path)
    runs.evaluate_candidates(first, [dict(spec)])
    runs.emit_viable(first)
    assert first.emitted, "le montage du test exige une émission"
    rec = first.book.active()[0]
    runs.evaluate_transitions(first, {"RENDER": rec.stop * 0.95})
    first.book.commit()
    assert first.book.all()[0].state_value is State.INVALIDATED

    # 2e run : même actif, même horizon, stop non approfondi -> bloqué.
    second = _ctx(tmp_path)
    runs.evaluate_candidates(second, [dict(spec)])
    runs.emit_viable(second)
    if not second.emitted:
        msg = runs.nothing_to_do_reason(second)
        assert "bloqué" in msg
        assert "ne franchit les conditions de viabilité" not in msg


# ══════════════════════════════════════════════════════════════════════════
# Le message atteint réellement le lecteur
# ══════════════════════════════════════════════════════════════════════════

def test_the_reason_reaches_the_mail_and_the_telegram_message(tmp_path, full):
    from src.telegram_bot import notify
    ctx = _ctx(tmp_path)
    runs.evaluate_candidates(ctx, [_spec("DUST", tier=4)])
    msg = runs.nothing_to_do_reason(ctx)
    text = notify.build_message(
        {"date_label": "lundi 10 août 2026", "banner": None,
         "top_action": None, "nothing_to_do": msg, "rejections": [],
         "transitions": [], "intraday": [], "metrics": [],
         "book": {"active": [], "active_count": "0"}}, "morning")
    assert "Aucun geste." in text
    assert "tier 4" in text

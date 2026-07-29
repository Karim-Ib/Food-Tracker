"""Dynamic target lines for the weight chart.

These replaced a hardcoded 100/95/90/86 ladder that only made sense from one
particular starting weight. The first test pins the replacement against that
original set, so the feature that generalised it did not also change what the
original user sees.
"""
import pytest
from app.services.weight_trend import build_targets


def kgs(targets) -> list[float]:
    return [kg for kg, _label in targets]


def test_reproduces_the_original_hardcoded_ladder():
    """Reference series: max measured 108.2, goal 86 -> the old 5-kg ladder."""
    assert kgs(build_targets(max_weight=108.2, goal=86)) == [105, 100, 95, 90, 86]


def test_no_goal_means_no_target_lines():
    assert build_targets(max_weight=108.2, goal=None) == []


def test_goal_line_is_always_present_and_labelled():
    targets = build_targets(max_weight=100.0, goal=82.5)
    labels = dict((kg, label) for kg, label in targets)

    assert 82.5 in labels
    assert labels[82.5] == "82.5 kg · goal"
    assert all("goal" not in label for kg, label in targets if kg != 82.5)


def test_line_count_stays_readable_across_wildly_different_spans():
    """The step grows with the span; the number of lines does not."""
    for max_weight, goal in [(70, 65), (108, 86), (150, 80), (200, 70), (95, 93)]:
        targets = build_targets(max_weight=max_weight, goal=goal)
        assert 1 <= len(targets) <= 6, f"{max_weight}->{goal} gave {len(targets)}"


def test_intermediate_lines_snap_to_a_round_grid():
    """Waypoints read as 90/95/100, not 88.4/93.4/98.4."""
    targets = build_targets(max_weight=108.4, goal=88.4)
    waypoints = [kg for kg, _ in targets if kg != 88.4]

    assert waypoints, "expected intermediate lines"
    assert all(kg % 5 == 0 for kg in waypoints), waypoints


def test_grid_line_crowding_the_goal_is_dropped():
    """A waypoint within half a step of the goal would overlap its label."""
    # goal 89 with a 5-kg step puts the grid at 90 — only 1 kg away.
    assert 90 not in kgs(build_targets(max_weight=109.0, goal=89.0))
    # goal 86 leaves 4 kg of clearance, so 90 survives.
    assert 90 in kgs(build_targets(max_weight=109.0, goal=86.0))


def test_targets_run_upward_when_the_goal_is_a_gain():
    targets = kgs(build_targets(max_weight=70.0, goal=80.0))

    assert max(targets) == 80.0
    assert all(70.0 <= kg <= 80.0 for kg in targets)
    assert len(targets) > 1, "a gaining goal should still get waypoints"


def test_zero_span_yields_only_the_goal_line():
    """Exact equality is the only zero-span case — there is nothing to ladder.

    Note that `max_weight` is the HIGHEST ever recorded, not the current weight,
    so hitting a weight-loss goal does not collapse the span: the band stays
    anchored at the old peak and the waypoints remain meaningful.
    """
    assert build_targets(max_weight=86.0, goal=86.0) == [(86.0, "86 kg · goal")]


def test_no_target_sits_at_or_above_the_highest_measured_weight():
    """A line drawn through the top data point is noise, not information."""
    for max_weight, goal in [(108.2, 86), (99.9, 70), (150, 80)]:
        assert all(kg < max_weight for kg in kgs(build_targets(max_weight, goal)))


def test_targets_are_ordered_high_to_low():
    targets = kgs(build_targets(max_weight=108.2, goal=86))
    assert targets == sorted(targets, reverse=True)


@pytest.mark.parametrize("goal", [86, 86.0, "86"])
def test_goal_accepts_the_numeric_types_the_db_layer_produces(goal):
    """Decimal from Postgres, float from JSON, str from a lax caller."""
    assert kgs(build_targets(108.2, float(goal)))[-1] == 86.0

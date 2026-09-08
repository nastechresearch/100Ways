from hundredways.durability import decide


def test_runs_daily_when_24_hours_elapsed():
    decision = decide(commits_since_base=3, seconds_since_check=24 * 60 * 60, burst=False)
    assert decision.run is True
    assert decision.burst is False


def test_500_commits_activates_burst_mode():
    decision = decide(commits_since_base=500, seconds_since_check=60, burst=False)
    assert decision.run is True
    assert decision.burst is True


def test_burst_runs_each_50_commit_step():
    decision = decide(commits_since_base=50, seconds_since_check=60, burst=True)
    assert decision.run is True
    assert decision.burst is True


def test_burst_waits_below_50_commits():
    decision = decide(commits_since_base=49, seconds_since_check=60, burst=True)
    assert decision.run is False
    assert decision.burst is True

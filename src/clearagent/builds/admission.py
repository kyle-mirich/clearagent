from clearagent.builds.scoring import CandidateEvaluation


# Quality admission gates. A candidate may only be deployed when its holdout
# evidence clears both bars: enough cases pass outright, and required
# behaviors still hold across nearly all of them.
MIN_HOLDOUT_PASS_RATE = 0.8
MIN_REQUIRED_BEHAVIOR_PASS_RATE = 0.8


def candidate_is_eligible(holdout: CandidateEvaluation | None) -> bool:
    if holdout is None:
        return False
    return (
        holdout.pass_rate >= MIN_HOLDOUT_PASS_RATE
        and holdout.required_pass_rate >= MIN_REQUIRED_BEHAVIOR_PASS_RATE
    )

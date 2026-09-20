# autoresearch run 2 — no measurable gain, and why that is the finding

Five iterations against a 40-case benchmark, then the benchmark itself turned
out to be the problem.

| iter | single-run metric | change |
|---|---|---|
| 0 | 0.4750 | baseline |
| 1 | 0.5000 | affordance: no `use_item` with an empty hand |
| 2 | 0.5000 | wording: `use_item`/`aim` criteria made consequential |
| 3 | 0.4750 | `out_of_frame` added to the act state (+20k tokens) |
| 4 | 0.4250 | consequence moved into the act instructions |

## The deltas were noise

Five runs of the *same code* scored 0.450, 0.500, 0.525, 0.475, 0.500. With 40
binary cases the standard error is sqrt(0.25/40) = 0.079, so one sigma is wider
than every delta in the table. The loop was measuring sampling variance and
calling it progress.

Rebuilt at 196 cases (1 sigma = 0.036) and measured properly:

    baseline  0.5233
    iteration 1  0.5204

The kept change is neutral. It stays in because it is right on principle — a
verb that cannot do anything should not be offered — but it is not an
improvement anyone should claim.

## Two bugs in the measuring tool, both mine

- **Failed API calls were scored as wrong answers.** At 196 cases the run hit
  sustained rate limits, and the scorer silently turned 429s into zeros: one
  pass read 0.0714 and another 0.0051, which looked like catastrophic
  regressions and were nothing of the kind. Calls now retry with backoff, and a
  run with more than 10% failures refuses to print a score at all.
- **Quotas filled from the oldest traces**, so freshly recorded situations could
  never enter the benchmark. That is why the aiming case sat at zero examples
  while it was being recorded.

## What holds up

**Affordance changes move behaviour; wording changes do not.** Across both runs
and the whole session, every real gain came from removing an option that could
not work. Three attempts to explain the world in better prose were neutral or
harmful.

**The crosshair signal is real but dominated.** The same aiming states, asked
with and without `looking_at`:

    with looking_at     picks={'use_item': 8}  mean P(aim)=0.133
    without looking_at  picks={'use_item': 8}  mean P(aim)=0.059

The field more than doubles the probability of aiming and still loses every
time. The state is not missing the information; `use_item` simply wins whenever
a usable item is in hand.

## Where the benchmark stands

196 cases, 1 sigma 0.036, ~$0.02 and a few minutes per run.

    aimed_at_a_block_with_snowball_held    0/16
    snowballs_carried_but_not_held         7/45
    container_holds_something_wanted      16/45
    container_holds_nothing_wanted        34/45
    snowball_held_and_nothing_in_the_way  45/45

The two worst are the two things this session never solved, which is what a
benchmark is for.

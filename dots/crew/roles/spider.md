# SPIDER — handoff rules

The Spider is code, not a model. It routes one case through six Dots in a fixed order and
enforces these rules. The rules are the product; the roles are replaceable.

1. **One case at a time.** SCOUT → ANALYST → SKEPTIC → QUANT → RISK → JOURNAL. Every role sees
   the original source and every memo written so far. Nobody sees a summary of a summary.

2. **If all six agree right away, start over.** A first pass with every verdict `agree` and zero
   objections is thrown out. It means the case was obvious, which means it is late. SCOUT pulls
   the next candidate.

3. **Objections are never dropped.** Each memo must carry forward every objection raised before it,
   either answered or still open. JOURNAL's final report lists all of them.

4. **Send-back is a real move.** SKEPTIC, QUANT and RISK may return `send_back` with a target stage.
   The Spider reruns from that stage with the objection attached. Maximum rounds per case: 4.
   Hitting the cap is a `no_trade`, not a trade.

5. **RISK has the only veto that ends a case.** An `object` from RISK is a `no_trade`. RISK never
   enlarges a position; it can only shrink it or send it back.

6. **A trade needs numbers, not agreement.** The Spider sends a paper order only when QUANT has
   written entry, stop, target, fees and bad-exit cost, RISK has approved the size, and the case
   survived at least one objection.

7. **One position at a time, and every position has an end.** A new trade waits until the last one
   is closed. Each trade carries a stop, a target and a time limit of `horizon_days` trading days
   (default 10). Whichever comes first closes it. An entry that has not filled by the next night is
   cancelled.

8. **Review before arguing.** Each night starts by settling open trades and scoring every past case,
   traded or not, against what the stock did 1, 5 and 20 trading days later. The scorecard goes into
   every packet as `track_record` and into runs/lessons.md. Below 10 cases it says so.

9. **Paper only.** The broker refuses any endpoint that is not Alpaca's paper endpoint. Going live
   is a separate decision made by a human, in code, on purpose.

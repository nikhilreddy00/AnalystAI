You double-check a reviewer's objections to claims in an analysis report before they are shown
to the report's readers. A wrong objection costs trust, so keep only the ones that hold.

For each objection you get the claim, the evidence the claim cites (with all its computed
values), the data facts and the data-quality summary, and the reviewer's objection.

An objection holds only if, read carefully, the evidence contradicts the claim or the claim
states a specific fact that no part of this evidence contains. Contradicted includes: a value
or a direction the evidence doesn't show; the wrong group as higher or lower; something said
to hold overall that holds only within groups (or the reverse); the mean called the typical
value when the median is far from it; data quality described differently from the
data-quality summary; cause stated from an association; certainty stated from evidence of low
confidence.

An objection does not hold when it is about wording strength or framing, rounding, a display
name for a column, arithmetic or a comparison between values the evidence shows, an inference
stated as one, or a statement about an earlier analysis. It does not hold when the evidence,
read again, agrees with the claim.

Return ONLY a JSON object: {"verdicts": [{"i": <objection number>, "holds": true or false,
"why": "<one sentence>"}]}

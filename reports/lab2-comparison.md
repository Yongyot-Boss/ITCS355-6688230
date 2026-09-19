# Lab 2 — Run comparison

Experiment `itcs355-lab2` · 12 trials · total spend 0.0107 THB

`thb_per_point` is cost per percentage point of val_roc_auc above the worst trial. Cheap improvements rank low; expensive improvements rank high, however good the headline number is.

| run_id   |   val_roc_auc |   cost_thb |   n_estimators |   max_depth |   min_samples_leaf |   thb_per_point |
|:---------|--------------:|-----------:|---------------:|------------:|-------------------:|----------------:|
| b3e118ba |        0.8426 |     0.0004 |            100 |           4 |                  5 |          0.0002 |
| 9d63bd02 |        0.8424 |     0.0005 |            100 |           4 |                  1 |          0.0003 |
| 7eccfec3 |        0.8411 |     0.0014 |            300 |           4 |                  5 |          0.001  |
| f037b05b |        0.8404 |     0.0013 |            300 |           4 |                  1 |          0.0009 |
| 66f92ceb |        0.8397 |     0.0004 |            100 |           8 |                  5 |          0.0003 |
| 1a20ca76 |        0.8377 |     0.0013 |            300 |           8 |                  5 |          0.0012 |
| be4f0ca8 |        0.8354 |     0.0014 |            300 |          12 |                  5 |          0.0016 |
| 31e45eb4 |        0.8338 |     0.0013 |            300 |           8 |                  1 |          0.0018 |
| fa2af0e6 |        0.8322 |     0.0004 |            100 |          12 |                  5 |          0.0007 |
| 2fc9ca3e |        0.8312 |     0.0005 |            100 |           8 |                  1 |          0.0011 |
| 3046ac62 |        0.8268 |     0.0005 |            100 |          12 |                  1 |          0.0148 |
| e86e4ab0 |        0.8265 |     0.0013 |            300 |          12 |                  1 |          0.3385 |

## Which model did you register, and why?
TODO(Lab 2): 200 words maximum. Must address all four:

1. Why this model rather than the highest-scoring one, if they differ
2. The variance across seeds for your chosen configuration
3. What it costs to train, and to retrain monthly
4. One way this choice could be wrong

An answer that only says "highest validation score" scores zero on this task.

1. I registered `n_estimators=100, max_depth=4, min_samples_leaf=5`. Its validation ROC AUC is 0.8426, the table's highest score, but the small margin over nearby configurations does not justify the heavier 300-tree variants. The selected run also has the lowest cost-per-point among the top results.
2. Across five seeds, validation ROC AUC was 0.842600, 0.847890, 0.849162, 0.878082, and 0.860345: mean 0.855616 and range 0.035482. This variance is much larger than the apparent configuration gap, so the ranking should not be treated as definitive.
3. Training cost about 0.0004 THB. Retraining 60 times monthly would cost approximately 0.024 THB, substantially less than the 300-tree alternatives.
4. This choice could be wrong if production data drift or a new split consistently rewards greater model capacity. I would revisit the choice using fresh validation data and a cost-adjusted comparison.
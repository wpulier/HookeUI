# Official offline packaging runtime

This environment pins Arc Research Institute's MIT-licensed `vcc-cli==0.3.0`
from [its published distribution](https://pypi.org/project/vcc-cli/0.3.0/).
The hashed lock covers transitive dependencies; each retains its own terms.
No credentials, model weights, input data or vendored upstream implementation
are included. Installation is separate from scientific operation admission.

`matrix-package-vcc` calls the same `vcc.prep.run_prep` implementation as the
official `vcc prep` command, in the confined scientific worker process. It uses
all default final count checks and produces a real `.vcc`, then decompresses
and independently checks the actual packaged matrix. It never calls submission,
authentication or upload code. Counts remain float32 whole values; exact gene
order avoids upstream reorder copies. Other dtypes/storage and extra annotations
require explicit preparation before admission.

The existing app-owned matrix boundary is extended deliberately: the general
one-folder RGB adapter interface does not accept retained matrix files. This
does not enable arbitrary uploaded executable adapters.

Synthetic tests qualify format/validation behavior only. They do not establish
full-density resource use, predictive accuracy, cloud image readiness or organizer
acceptance. Full-shape runs must retain actual worker RSS/disk/time and transfer
measurements beside this capability's true matrix stored-entry count.

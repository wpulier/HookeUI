# Screener (H14 study)

Eligibility follows the frozen design §2.1. The screener is scored before enrolment, by someone other than the moderator
who will run the participant's sessions. Record answers against the participant code only.

1. Which best describes your role? graduate student · postdoc · technician · staff scientist · imaging-core staff · PI
   · other (ineligible if other)
2. Do you work with images of biological samples from a microscope? yes / no (no: ineligible)
3. Over the past 6 months, how often have you analysed microscopy images? at least monthly / less often (less often:
   ineligible)
4. Have you ever used, or seen a demonstration of, Hooke, HookeUI, ObservationUI or MicroscopeMind? yes / no (yes:
   ineligible)
5. Are you employed by, or have you contributed to, Hooke or napari? yes / no (yes: ineligible)
6. Did you take part in this study's pilot? yes / no (yes: ineligible)
7. Over the past 6 months, how often have you used napari? at least monthly (stratum: regular) / less often or never
   (stratum: naive)
8. Do you use assistive technology with a computer? If yes, which (optional; reported separately, never a reason to
   exclude).
9. **Python item (2 minutes, scored pass or fail).** Show this script and ask: "Change it so it prints the mean of
   only the values above 100, then say what it would print."

   ```python
   import numpy as np
   values = np.array([12, 150, 300, 90, 101])
   print(values.mean())
   ```

   Pass if the participant writes, or clearly says, `print(values[values > 100].mean())` or an equivalent, and says
   about 183.67. Any working equivalent passes; syntax slips the participant corrects themselves are fine.

Eligible only if 1–6 and 9 pass. Quotas (design §2.2): at least 48 naive and at least 36 regular among completers;
no lab more than 15%, no institution more than 40%.

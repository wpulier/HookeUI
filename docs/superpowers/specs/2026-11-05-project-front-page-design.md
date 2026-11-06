# Project front page

Approved by Will on 5 Nov 2026 (mock v3). It replaces the Project overview's bookkeeping states ("Execution needs
checking", "Assignment ownership needs checking", counters) with a page that answers, in order:

1. **What this Project is.** Its name and one sentence, written by the owner or an agent (`about`).
2. **The Project itself.** One retained HTML file chosen as *the Project's page* (`page`): the Project at its most
   experiential, whatever its kind (a challenge entry, a proof, a short paper with a time-lapse, a lesson). It shows as
   a sandboxed preview with Open the page. Hooke serves it under the researcher-lesson content policy. When the
   Project has results newer than the page, it says so and offers an agent brief to update it. Hooke makes no model
   call of its own.
3. **Where the work is.** Four columns, the same for every Project:
   - **Now:** admitted runs, working researchers and live assignment claims.
   - **Just finished:** the newest reports, findings and finished runs.
   - **Next in the plan:** reports to review, unanswered questions, assignments ready to claim, paused Studies, lapsed
     claims to close or continue, and the Project's own planned items (`next`).
   - **Your own next step:** a box that drafts a request to the Assistant, Start a Study, and browse inputs.
4. **How it was made.** The Studies, oldest first, each opening its own story.

## Records

One `project-front` document per Project. Its identity is derived from the session, and it lives in the same
revisioned ProjectStore as Studies, so it travels with export, backup and publication. It holds:

- `about`: at most 600 characters.
- `page`: an exact `{id, revision, digest}` of a retained `.html` source file, or nothing.
- `next[]`: at most 64 items, each `{id, text, study?, when?, by, byLabel, at, done?}`.

Every change is attributed. Owner changes come from the studio route `/api/project-front`; agent changes come from the
door command `project.front`, which needs write access for a change and read access for `read`. The studio reads the
record with `GET /api/project-front`. The page is served by `GET /api/scientific-files/{id}/page` as `text/html` under
a sandbox policy that loads nothing from the network; anything else is refused.

## False alarms

- **A claimant's activity renews its leases.** Any Study write by the agent holding running assignments renews them
  for 300 s: a report, a challenge or an evidence read. Re-claiming already did this.
- **A lapsed lease is not an execution problem.** When a lapsed lease has no admitted operation it is ownership
  bookkeeping. It shows as "close or continue" under Next in the plan, never as "Execution needs checking".

## Not in scope

- Hooke writing the page itself with a model.
- Candidate, rule and decision records. The capabilities session owns `frozenRules[]`; a Study's own page shows them
  later.
- Publishing the page to the website.

# 16. One mind, one I

**Context.** MicroscopeMind is several models working together:
- a voice that can't take images (GPT-Live);
- the running look, which sees;
- the fast pass, which acts on spoken words;
- the brain, which looks closer and plans;
- the instruments (EdgeTAM, Cellpose-SAM, SAM 3.1, the motion measurer…);
- the researchers.

Each was told a different story about who it is:
- the voice, "this voice call receives text context, not camera or workspace images" and "the app is your hands" (7a386862, 2026-10-09);
- the running look, "the eyes … for the assistant's voice, which cannot see it";
- the researchers, nothing about the app's voice at all.

In the 2026-10-11 demo, the user asked "what's the little dot". The running look had said "prominent dark nuclei inside each cell" 15 s earlier. The voice still said "One sec, I'll check that", handed the question over, and promised to point at something it never pointed at. The same session said "I see", "what the app sees", "let me take a look" and "one of our AI researchers". Who saw what, and who did what, could not be told apart (`~/MicroscopeMind/reviews/2026-10-11-demo/`).

**Decision.**
- **One mind speaks as "I".** MicroscopeMind is one assistant looking through the microscope with the user. Every model that speaks to the user is told the same paragraph (`prompts.MIND`): the voice, the brain's answers and rewrites, and the researchers. Models that make data (the running look, naming, the fast pass, the outline check) are not given it; their wording changes only on their benches.
- **The parts:**
  - its **eyes** are the running look;
  - its **voice** speaks from what the eyes last saw;
  - its **reflexes** (the fast pass) act on what it already sees;
  - its **closer look** (the brain) looks again and plans;
  - its **instruments** outline, count, measure and follow;
  - its **researchers** are helpers it sends off, credited by number;
  - a web photo is a real photo with its source; a researcher's picture is said to be made.
- **The voice is the one who sees, honestly.** Its notes are what it sees now, so it answers as someone looking and never narrates checking. The facts 7a386862 added stay:
  - a note has an age;
  - a window listing is not the window's pixels;
  - earlier notes are not continuous sight.

  Detail a note doesn't hold is a closer look through the app, and the voice says so by what it does, not by "let me look".
- **The Capabilities page reads the same way:** You / I / I, in the background / My researchers.
- Parts are named when asked ("how did you do that?") and in Activity, from the record (`known.chain`, `resources`).

**Cost.**
- The paragraph is about 90 tokens in each prompt that writes the user's words.
- Prompt goldens change.
- The voice is trusted to answer from notes, so how fresh and complete the notes are becomes what makes it right. That is measured by the voice journeys bench and the notes-answer-it bench before any default moves.

Design: `~/MicroscopeMind/reviews/2026-10-11-demo/PLAN.md` and `NORTH-STAR.md`; ADR 0006 and 0014.

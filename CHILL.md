# The Low-Cortisol Project Standard

_This project follows the Low-Cortisol Project Standard from [chill-foss](https://github.com/JustBeanie/chill-foss). A copy lives here so you can read it without leaving the repo._

> Software is supposed to be the fun part.

This is a short, adoptable standard for running a free software project that people actually want to contribute to: one that stays about the software, keeps the stress low, and doesn't slowly rot.

**It's an addition, not a replacement.** Keep your license, your existing docs, your process and your own rules, and layer these on top: a section in CONTRIBUTING, a short Code of Conduct, a FUNDING file, a badge. Nothing here asks you to tear anything down.

Take as much of it as you like. It's CC0, so copy it into your repo, change the words, and don't credit us. If you adopt it, you can add the [badge](#badge).

---

## The idea

Most projects don't die from a bad technical decision. They die from **friction**. Contributors bounce off hostile rules. Maintainers burn out on drama. Issues pile up with no answer. A once-fun repo slowly becomes a place nobody wants to be.

A low-cortisol project removes friction wherever it can. It sets a few calm rules, writes them down, follows them the same way for everyone, and gets back to building.

## The eight principles

### 1. Judge the work, not the workflow

Review a contribution on whether it's correct, tested, readable, and wanted. How someone wrote it (editor, language server, autocomplete, AI assistant) is their business. **The contributor is responsible for every line, however it was produced.**

Welcoming AI doesn't mean anything goes. AI makes it cheap to produce a huge diff, and a huge diff nobody can review is pure stress. Chill projects set limits on the **contribution**, not the tool.

**In practice:**
- AI-assisted contributions are welcome. Disclosure is optional.
- **Keep it reviewable.** One focused change per PR. If it's large, split it or open an issue first to agree on the plan. Maintainers may close drive-by mega-PRs without a full review.
- **It has to make sense.** The contributor can explain every change and why it's needed. No invented APIs, no unrelated churn, no reformatting files you didn't need to touch, no tests weakened to make them pass.
- **Name your red flags, and stop there.** Concrete signs are fair grounds for rejecting low-effort AI output: large amounts of unnecessary code, inconsistent style, APIs that don't exist, comments left over from prompting. Don't add a catch-all like "or other signs of AI". A rule that can't be checked can't be followed, and that's how good-faith contributors get burned.
- **No unreviewed bot traffic.** Automated PRs, issues, and comments need a human who has read them and stands behind them.
- Low-effort spam gets closed for being low effort, not for the tool behind it.

### 2. Keep the repo about the software

The README, issue tracker, release notes, and website exist to help people use and build the project. Political, social, or cultural campaigns belong on the maintainers' personal channels, whatever the cause and whichever side it's on.

**In practice:** no banners, slogans, or causes in project spaces. No protestware, ever: code that acts on users based on who or where they are breaks the trust that free software depends on. Personal views stay personal, and nobody gets judged in the project for views they hold outside it.

### 3. Be open about the money

Who pays for development shapes priorities. Say who funds the project, and on what terms, in one obvious place.

**In practice:** a `FUNDING.md` or README section that names every grant, sponsor, and fiscal host, including government and state-backed funds, with any conditions attached. Funders never get a say over the roadmap, and you say so.

### 4. A short Code of Conduct, enforced the same way for everyone

The Code of Conduct covers behavior in project spaces: be civil, stay on topic, no harassment. It's not a statement of beliefs, and it doesn't police what people do elsewhere.

**In practice:** a short Code of Conduct about behavior, with a real way to report problems. The [chill-foss Code of Conduct](CODE_OF_CONDUCT.md) is a drop-in: be friendly, talk about the work not the person, no harassment or spam, outside causes stay outside, and everyone is judged only by how they act in the project. No doxxing, and no deplatforming: nobody gets banned for their views or for what they do elsewhere. Apply it to conduct, never to anyone's identity or opinions.

### 5. Answer people

Silence is the most stressful response a contributor can get. Even "not now" is better than nothing.

**In practice:** label new issues and PRs within a week, even if it's only `needs triage` or `help wanted`. Close what you won't do, with a one-line reason. Mark stale work stale instead of letting it sit forever.

**Let AI do the tidying.** AI is good at keeping things organized: triaging and labeling issues, spotting duplicates, summarizing long threads, drafting changelogs and release notes, keeping docs in step with the code. A human still makes the decisions, but nobody should spend an evening hand-sorting a backlog.

### 6. Make the first contribution easy

**In practice:** a `CONTRIBUTING.md` that gets someone from clone to passing tests in five minutes. Issues labeled `good first issue` and `help wanted`. CI that tells contributors what's wrong in plain words. An `AGENTS.md` or `llms.txt` so contributors' tools understand the project too.

### 7. Don't let it rot

**In practice:**
- **Releases:** cut them on a predictable rhythm, even small ones, and keep a changelog.
- **Dependencies:** update them on a schedule, not only when something breaks.
- **Bus factor:** at least two people can merge, release, and reach the domain and the signing keys.
- **Exit plan:** if you're stepping back, say so in the README and hand the project over or archive it cleanly. Don't let it go quiet.

### 8. Write the rules down, then follow them

Unwritten rules cause the most stress, because nobody knows which ones apply until they break one. Every policy above should live in a file in the repo, and maintainers should follow the same rules as everyone else.

---

## Self-check

Score one point for each line that's true. **11 or more is low cortisol.**

- [ ] The contribution policy accepts AI-assisted work, with disclosure optional or lightly encouraged
- [ ] Contributions are reviewed on quality, with no tool-based gatekeeping
- [ ] The policy sets limits on the contribution itself: focused, reviewable PRs that the author can explain
- [ ] Every rejection criterion is written down and checkable, with no catch-all "other signs" clause
- [ ] The project's site, README, and release notes carry no political or cause messaging
- [ ] The project has never shipped protestware
- [ ] Funding sources are listed in one public place
- [ ] Funders have no control over the roadmap, and the docs say so
- [ ] The Code of Conduct covers behavior in project spaces only, and it lists a real contact
- [ ] Nobody is banned for their views or off-project activity, and doxxing or deplatforming campaigns aren't tolerated
- [ ] New issues and PRs get a first response within a week
- [ ] `CONTRIBUTING.md` gets a newcomer to passing tests quickly
- [ ] There's a release in the last 6 months, or a stated reason why not
- [ ] At least two people can merge and release
- [ ] All of the above is written down in the repo

---

## Drop-in snippets

### `CONTRIBUTING.md` — AI policy

```markdown
## Using AI tools

You're welcome to use AI assistants, code completion, or any other tools to
help write your contribution. Disclosure is optional. You are responsible
for everything you submit: make sure you understand it, that it's tested,
and that you have the right to contribute it. Contributions are reviewed on
quality, not on how they were produced.

A few limits keep reviews sane:

- One focused change per pull request. For anything large, open an issue
  first so we can agree on the approach, then send it in reviewable pieces.
- Be ready to explain every change and why it's needed.
- Don't touch unrelated code, reformat files you didn't change, or weaken
  tests to make them pass.
- Automated PRs, issues, and comments need a human who has read them.

Oversized or unexplained changes may be closed without a full review.
```

### `README.md` — project scope

```markdown
## Scope

This repository is about <project>. Project spaces (issues, discussions,
releases, and this README) stay on that topic. Personal views are welcome on
your own channels.
```

### `FUNDING.md`

```markdown
# Funding

| Funder | Type | Period | Amount | Conditions |
|---|---|---|---|---|
| <name> | <individual / company / foundation / government> | <dates> | <amount or "undisclosed"> | <any strings attached, or "none"> |

Funders have no say over the project's roadmap or decisions.
```

### Badge

If your project passes the self-check, add this to your README and copy this file in as `CHILL.md`, so the badge links somewhere real:

```markdown
[![cortisol: low](https://img.shields.io/badge/cortisol-low-6B8F71?style=flat&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI%2BPHBhdGggZmlsbD0iI2ZmZiIgZD0iTTQgMjFjLS42LTYgMi4yLTExLjYgOS0xNC41IDMtMS4zIDUuOC0xLjYgNy0xLjUuNCA0LjgtMS4yIDkuNi01IDEyLjQtMi43IDItNiAyLjYtOC42IDIuMUw0IDIxem0yLjItMi4zYzIuNi0zLjggNi02LjcgMTAuMy04LjgtNC42IDEuMy04LjQgNC4zLTEwLjMgOC44eiIvPjwvc3ZnPg%3D%3D)](CHILL.md)
```

Use the same `style=` as your other shields.io badges (`flat`, `flat-square`, `for-the-badge`) so it fits in.

Listed in chill-foss? There's a [`chill-listed` badge](https://github.com/JustBeanie/chill-foss/blob/main/badges/chill-listed.svg) too.

---

## Why this exists

[chill-foss](https://github.com/JustBeanie/chill-foss) catalogs free and open source projects. The catalog's Chill badge identifies entries with zero flags, including projects with a neutral stance on AI. This standard offers practices for running a project people enjoy working on.

"""Generate a 5-page PDF migration plan for moving local knitweb work to Knitweb/pulse."""

from __future__ import annotations

from datetime import date
from fpdf import FPDF
from pathlib import Path


class MigrationPlanPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)
        # Use fpdf's built-in core fonts (Helvetica/Courier) so the script works
        # on any platform without system TTF files or font vendoring.

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, "Knitweb Local Repository Migration Plan - Knitweb/pulse", align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_xy(self.w - self.r_margin - 40, self.t_margin - 10)
        self.cell(40, 10, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, "Internal migration planning document - do not distribute", align="C")

    def title_page(self) -> None:
        self.add_page()
        self.set_font("Helvetica", "B", 24)
        self.set_text_color(30, 30, 30)
        self.ln(60)
        self.cell(0, 15, "Local Repository Migration Plan", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 16)
        self.set_text_color(60, 60, 60)
        self.cell(0, 12, "Migrating in-flight branches to Knitweb/pulse", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(20)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(80, 80, 80)
        self.multi_cell(
            0,
            7,
            (
                "The legacy remote febuz/knitweb has been archived and superseded by "
                "the Knitweb organization repository Knitweb/pulse. This document describes "
                "the current local state, the target state, and the step-by-step procedure "
                "to rebase, validate, and open pull requests for all in-flight work."
            ),
            align="C",
        )
        self.ln(30)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, "Prepared by: Kimi Code CLI", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, f"Date: {date.today().isoformat()}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, "Target remote: git@github.com:Knitweb/pulse.git", align="C", new_x="LMARGIN", new_y="NEXT")

    def section_title(self, title: str) -> None:
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(30, 58, 138)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 6, text)
        self.ln(3)

    def bullet(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        indent = 6
        x = self.l_margin + indent
        self.set_x(self.l_margin)
        self.cell(indent, 6, "-", align="L", new_x="RIGHT")
        self.multi_cell(self.w - self.r_margin - x, 6, text)
        self.set_x(self.l_margin)

    def code_block(self, text: str) -> None:
        self.set_fill_color(245, 245, 245)
        self.set_font("Courier", "", 9)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 5, text, fill=True)
        self.ln(3)

    def warning(self, text: str) -> None:
        self.set_fill_color(255, 251, 235)
        self.set_draw_color(245, 158, 11)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(146, 64, 14)
        self.multi_cell(0, 6, f"  {text}", fill=True, border="L")
        self.ln(3)

    def note(self, text: str) -> None:
        self.set_fill_color(239, 246, 255)
        self.set_draw_color(59, 130, 246)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 64, 175)
        self.multi_cell(0, 6, f"  {text}", fill=True, border="L")
        self.ln(3)


def main() -> None:
    pdf = MigrationPlanPDF()
    pdf.title_page()

    # -----------------------------------------------------------------------
    # Page 2: Current state inventory
    # -----------------------------------------------------------------------
    pdf.add_page()
    pdf.section_title("1. Current State Inventory")
    pdf.body_text(
        "The local repository was originally cloned from the archived remote "
        "febuz/knitweb. During the last session the remote was updated to "
        "Knitweb/pulse and the main branch was reset to the migrated upstream head. "
        "Several feature branches and a stash of unrelated work remain based on the "
        "old history and must be reconciled."
    )

    pdf.section_title("1.1 Active Local Branches")
    pdf.bullet("feat/lens-rlm-digest - MeTTa-inspired atomspace + KnitwebLensAdapter for virtualpc LLM agents (1 commit).")
    pdf.bullet("feat/pouw-quorum-settlement - verifier quorum and quorum-aware escrow settlement (2 commits).")
    pdf.bullet("feat/anchor-origintrail-backend - placeholder branch containing stashed anchor/Rosetta work in progress.")
    pdf.ln(3)

    pdf.section_title("1.2 Stashed Work")
    pdf.bullet("Stash: anchor-rosetta-wip - uncommitted files including anchor/__init__.py, anchor/origintrail.py, Rosetta Code loom, GitHub templates, CONTRIBUTING.md, and PROCESS_MANAGEMENT.md.")
    pdf.bullet("This work predates the migration and must be reviewed for relevance before rebasing.")
    pdf.ln(3)

    pdf.section_title("1.3 Divergence from Upstream")
    pdf.body_text(
        "Upstream main (Knitweb/pulse) is many commits ahead of the old febuz/knitweb main. "
        "The repository has grown from ~40 source files to more than 1,100 property tests. "
        "New modules that overlap with local work include:"
    )
    pdf.bullet("knitweb/interpret/ - content-addressed agent memory nodes (SkillNode, ProjectNode, etc.) and distillation.")
    pdf.bullet("knitweb/pouw/quorum.py - a full BFT supermajority quorum already exists, so the local quorum module may duplicate or complement it.")
    pdf.bullet("knitweb/lens/ - an empty package directory exists; the local lens implementation can land here.")

    # -----------------------------------------------------------------------
    # Page 3: Migration steps
    # -----------------------------------------------------------------------
    pdf.add_page()
    pdf.section_title("2. Migration Procedure")
    pdf.body_text(
        "The migration is performed branch-by-branch. Each branch is rebased onto the "
        "new upstream main, conflicts are resolved in favour of upstream semantics, tests "
        "are run, and the branch is force-pushed to Knitweb/pulse."
    )

    pdf.section_title("2.1 Prerequisite: Update Local References")
    pdf.code_block(
        "cd repo/knitweb\n"
        "git remote set-url origin git@github.com:Knitweb/pulse.git\n"
        "git fetch origin\n"
        "git checkout main\n"
        "git reset --hard origin/main"
    )

    pdf.section_title("2.2 Rebase Each Feature Branch")
    pdf.code_block(
        "# Lens branch\n"
        "git checkout feat/lens-rlm-digest\n"
        "git rebase main\n"
        "# resolve conflicts; prefer upstream APIs\n"
        "PYTHONPATH=src python3 -m pytest tests/property/test_lens.py -q\n"
        "git push --force-with-lease -u origin feat/lens-rlm-digest"
    )
    pdf.code_block(
        "# POUW quorum settlement branch\n"
        "git checkout feat/pouw-quorum-settlement\n"
        "git rebase main\n"
        "# Expect overlap with upstream knitweb/pouw/quorum.py\n"
        "PYTHONPATH=src python3 -m pytest tests/property/test_quorum_settlement.py -q\n"
        "git push --force-with-lease -u origin feat/pouw-quorum-settlement"
    )

    pdf.section_title("2.3 Decide on Stashed Work")
    pdf.bullet("Inspect the stashed files against upstream main to detect duplication (e.g., anchor/origintrail.py versus existing OriginTrail integration).")
    pdf.bullet("Split the stash into coherent topic branches: feat/github-templates, feat/anchor-origintrail, feat/loom-rosettacode.")
    pdf.bullet("Discard anything already present in upstream or rendered obsolete by the new interpret/ lens foundation.")

    # -----------------------------------------------------------------------
    # Page 4: Conflict handling and validation
    # -----------------------------------------------------------------------
    pdf.add_page()
    pdf.section_title("3. Conflict Resolution Rules")
    pdf.body_text(
        "Because the upstream repository evolved independently after the archive, some "
        "local files will conflict. Apply the following rules in order:"
    )
    pdf.bullet("Upstream API wins. If upstream already provides an equivalent module, drop the local implementation and adapt callers.")
    pdf.bullet("Preserve tests. A local test that exercises upstream behavior is valuable even if the implementation is replaced.")
    pdf.bullet("Rename on collision. If the local module has different semantics (e.g., MeTTa atomspace versus interpret/distill), rename or nest it under a distinct subpackage such as knitweb/lens/metta.")
    pdf.bullet("Keep pure-Python and dependency-free. Do not introduce new third-party dependencies that conflict with upstream's stdlib-only policy.")
    pdf.ln(3)

    pdf.warning(
        "The local feat/pouw-quorum-settlement branch defines knitweb.pouw.quorum, "
        "which already exists upstream with additional validation (ABSTAIN verdict, "
        "strict majority enforcement). Do not overwrite upstream quorum.py. Instead, "
        "rebase so that the local commit only adds quorum_settlement.py and its tests, "
        "re-using upstream Verdict/Outcome/tally."
    )

    pdf.section_title("4. Validation Gate")
    pdf.body_text("Before any branch is pushed, the following checks must pass:")
    pdf.bullet("Feature tests pass: PYTHONPATH=src python3 -m pytest tests/property/test_<feature>.py -q")
    pdf.bullet("Full property suite passes (subset if slow WebRTC tests are present): PYTHONPATH=src python3 -m pytest tests/property -q -k 'not webrtc'")
    pdf.bullet("No type regressions: python3 -m mypy src/knitweb/<changed_package> if mypy is available.")
    pdf.bullet("LOC report refreshed: python3 tools/loc_report.py")
    pdf.ln(3)

    pdf.note(
        "The upstream test suite contains ~1,157 tests. Running the full suite may take "
        "several minutes due to WebRTC transport tests. Use targeted tests during active "
        "development and run the full suite only as a final gate."
    )

    # -----------------------------------------------------------------------
    # Page 5: PRs, feedback loop, risks, rollback
    # -----------------------------------------------------------------------
    pdf.add_page()
    pdf.section_title("5. Pull Request and Feedback Loop")
    pdf.body_text(
        "After a branch is pushed to Knitweb/pulse, open one pull request per feature. "
        "Follow the repository's existing PR template and reference the originating "
        "archived repository only for historical context."
    )
    pdf.code_block(
        "gh pr create --base main --head feat/lens-rlm-digest \\\n"
        "  --title \"feat(lens): MeTTa-inspired atomspace for virtualpc agents\" \\\n"
        "  --body \"Rebases #<legacy-PR> onto Knitweb/pulse. Adds ...\""
    )
    pdf.bullet("Assign or request review from Claude / the maintainer team.")
    pdf.bullet("Monitor PR comments using gh pr view <number> --comments or the GitHub web UI.")
    pdf.bullet("Implement feedback in-place on the same branch; do not open a second PR for the same feature unless requested.")
    pdf.bullet("Never merge - per workflow instructions, merges are handled by Claude after changes are solid.")
    pdf.ln(3)

    pdf.section_title("6. Risk Assessment and Rollback")
    pdf.bullet("Risk: Rebase introduces silent semantic changes because upstream APIs moved. Mitigation: run feature tests and inspect diff against main after rebase.")
    pdf.bullet("Risk: Local quorum module conflicts with upstream. Mitigation: delete local quorum.py and adapt tests to upstream types.")
    pdf.bullet("Risk: Stashed work is partially obsolete. Mitigation: review each file before rebasing; discard duplicates.")
    pdf.bullet("Risk: Force-push overwrites shared history. Mitigation: use --force-with-lease and confirm no one else has pushed to the feature branch.")
    pdf.ln(3)

    pdf.section_title("7. Rollback Procedure")
    pdf.code_block(
        "# If a rebase goes wrong, abort or reset\n"
        "git rebase --abort\n"
        "git checkout <branch>\n"
        "git reset --hard <pre-rebase-backup-ref>\n"
        "# Or recreate from the original commit before migration\n"
        "git reflog | head -20"
    )

    pdf.section_title("8. Post-Migration Checklist")
    pdf.bullet("All local branches pushed to Knitweb/pulse with green CI.")
    pdf.bullet("Open PRs exist for feat/lens-rlm-digest and feat/pouw-quorum-settlement.")
    pdf.bullet("Stashed work split into coherent branches or discarded.")
    pdf.bullet("Local main tracks origin/main exactly.")
    pdf.bullet("Archived febuz/knitweb remote removed from local remotes.")

    out_path = Path(__file__).parent.parent / "docs" / "MIGRATION_PLAN_KNITWEB_PULSE.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    print(f"Wrote migration plan to {out_path}")


if __name__ == "__main__":
    main()

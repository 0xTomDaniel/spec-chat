import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "skill" / "shape-spec" / "scripts" / "validate-style.py"
FALLBACK = ROOT / "skill" / "shape-spec" / "assets" / "style" / "spec.css"
DEFAULT_STORY = '<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="no">Story</p>'


# mockups #fidelity: a drawn mockup's styles at production fidelity, each part a constant so a test can take it away.
TOKENS = (
    "[data-mockup-frame] { --ui-ink: #333333; --ui-action: #1d4ed8; --ui-font: Inter, system-ui, sans-serif;"
    " --ui-motion-duration: 150ms; --ui-motion-ease: ease-out; color: var(--ui-ink); }"
)
FONT = " [data-mockup-frame] { font: 16px/1.5 var(--ui-font); }"
MOTION = " [data-mockup-frame] button { transition: background var(--ui-motion-duration) var(--ui-motion-ease); }"
FOCUS = " [data-mockup-frame] button:focus-visible { outline: 2px solid var(--ui-action); }"
DISABLED = " [data-mockup-frame] button:disabled { color: var(--ui-ink); }"
REDUCED = " @media (prefers-reduced-motion: reduce) { [data-mockup-frame] { --ui-motion-duration: 0ms; } }"
MOCKUP_STYLE = TOKENS + FONT + MOTION + FOCUS + DISABLED + REDUCED
DEFAULT_FRAMES = (
    '<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-rule">'
    '<div data-anchor="desktop-card"><img src="avatar.png" alt="Ada"></div></section>'
    '<section data-mockup-frame="phone" data-anchor="phone">'
    '<div data-anchor="phone-card" data-illustrates="acceptance-rule user-stories"><button>Save</button></div></section>'
)
STEPS = (
    '<ol data-mockup-steps><li data-step="totals">Every team\'s total</li>'
    '<li data-step="team-open" data-step-by="desktop-card">Payments opens its last three weeks</li></ol>'
)
PROTOTYPE_BODY = (
    '<ul data-mockup-regions>'
    '<li data-anchor="totals" data-select="main .totals" data-illustrates="acceptance-rule">Weekly totals</li>'
    '<li data-anchor="team-panel" data-select="#team" data-illustrates="acceptance-rule">Team panel</li></ul>'
    '<ol data-mockup-steps><li data-step="totals">Every team\'s total</li>'
    '<li data-step="team-open">Payments opens its last three weeks<ul><li data-press=".team-card"></li></ul></li></ol>'
    '<aside data-mockup-questions><div data-spec-tbd data-anchor="question-trend" data-about="team-panel" data-step="team-open">'
    'Should the panel show a trend line? <button data-spec-choice="Totals only">Totals only (recommended)</button></div></aside>'
)

# mockups #markup-directions, #directions-file: a first round's directions, Table the agent's pick, the mockup itself, and
# Cards another direction, by its route in a prototype mockup or, in a drawn one, its own two frames naming it.
PICK = '<li data-direction="table" data-gives-up="Trends read slower than in cards" data-pick="Totals compare at a glance">Table</li>'
CARDS = '<li data-direction="cards" data-gives-up="Fewer teams fit above the fold">Cards</li>'
ROUTED = CARDS.replace("<li ", '<li data-route="/reports/weekly-cards" ')
CARDS_FRAMES = (
    '<section data-mockup-frame="desktop" data-direction="cards" data-anchor="cards-desktop" data-illustrates="acceptance-rule"></section>'
    '<section data-mockup-frame="phone" data-direction="cards" data-anchor="cards-phone" data-illustrates="acceptance-rule"></section>'
)


def directions(*items):
    return '<ul data-mockup-directions data-anchor="directions">' + "".join(items) + "</ul>"


def question(step, about="desktop-card"):
    return (
        f'<aside data-mockup-questions><div data-spec-tbd data-anchor="question-trend" data-about="{about}" data-step="{step}">'
        'Should the panel show a trend line? <button data-spec-choice="Totals only">Totals only (recommended)</button></div></aside>'
    )


class ShapeStyleProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = self.repo / "docs" / "specs" / "domains" / "example.spec.html"
        self.style = self.repo / "docs" / "specs" / ".style" / "spec.css"
        self.spec.parent.mkdir(parents=True)
        self.style.parent.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "spec-chat@example.test")
        self.git("config", "user.name", "Spec Chat Test")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ("git", "-C", str(self.repo), *args),
            check=True,
            text=True,
            capture_output=True,
        )

    def write_spec(self, inline=False, href="../.style/spec.css", extra="", body=DEFAULT_STORY, runtime="../.viz/runtime.js", path=None):
        local = "<style>body { background: navy; }</style>" if inline else ""
        link = f'<link rel="stylesheet" href="{href}">' if href else ""
        script = f'<script defer src="{runtime}"></script>' if runtime else ""
        (path or self.spec).write_text(f"<!doctype html><html><head>{link}{script}{extra}{local}</head><body>{body}</body></html>\n")

    def write_shaped_spec(self, story=DEFAULT_STORY, scope="traceability", current="", acceptance_heading="Acceptance criteria", acceptance_extra=' data-story="story"', boundary_extra="", boundary_scope="Observable scope is this review", scenario_text="When the rule is exercised", observable_text="The observable result is recorded", extra_criteria="", behavior="", **page):
        current_attr = f' data-current-slice="{current}"' if current else ""
        body = f"""<article class=\"spec\" data-spec-contract=\"shaped-sections-v1\"{current_attr}>
<section data-spec-section=\"user-stories\" data-anchor=\"user-stories\">
<h2>User stories</h2>{story}
</section>
<section data-spec-section=\"acceptance\" data-acceptance-scope=\"{scope}\" data-anchor=\"acceptance\">
<h2>{acceptance_heading}</h2>
<p data-acceptance-criterion data-anchor=\"acceptance-rule\"{acceptance_extra}><span data-acceptance-scenario>{scenario_text}</span><span data-acceptance-observable>{observable_text}</span></p>{extra_criteria}
</section>
<section data-spec-section=\"modular-boundaries\" data-anchor=\"modular-boundaries\">
<h2>Modular boundaries</h2>
<p data-modular-boundary data-anchor=\"boundary-rule\"{boundary_extra}><span data-boundary-responsibility>The spec owner defines the contract</span><span data-boundary-seam>The caller-facing seam is the review handoff</span><span data-boundary-dependency>The direction is self-contained</span><span data-boundary-scope>{boundary_scope}</span></p>
</section>
<section data-anchor=\"behavior\">
<h2>Behavior</h2>{behavior}
</section>
</article>"""
        self.write_spec(body=body, **page)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def empty_base(self):
        self.git("commit", "--allow-empty", "-qm", "base")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def validate(self, base, whole_spec=False):
        flags = ("--whole-spec",) if whole_spec else ()
        return subprocess.run(
            (sys.executable, str(VALIDATOR), *flags, str(self.repo), str(self.spec), base),
            text=True,
            capture_output=True,
        )

    def test_accepts_unchanged_established_shared_style(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("style=established", result.stdout)

    def test_accepts_new_byte_identical_fallback(self):
        base = self.empty_base()
        self.write_spec()
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("style=fallback", result.stdout)
        self.assertIn("stories=valid", result.stdout)

    def test_rejects_spec_local_style_block(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()
        self.write_spec(inline=True)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("may not contain page-level <style> blocks", result.stderr)

    def test_rejects_new_custom_shared_style(self):
        base = self.empty_base()
        self.write_spec()
        self.style.write_text("body { background: linear-gradient(navy, blue); }\n")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match the bundled fallback", result.stderr)

    def test_rejects_changed_established_style(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()
        self.style.write_text("body { color: #14213d; background: #eef3f8; }\n")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the exact review base", result.stderr)

    def test_rejects_missing_shared_style_link(self):
        base = self.empty_base()
        self.write_spec(href="")

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one repository-relative .style/spec.css", result.stderr)

    def test_rejects_an_additional_custom_stylesheet(self):
        base = self.empty_base()
        self.write_spec(extra='<link rel="stylesheet" href="../custom.css">')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one shared stylesheet", result.stderr)

    def test_rejects_changed_governing_spec_without_explicit_stories(self):
        base = self.empty_base()
        self.write_spec(body="<p>Unanchored outcome</p>")
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no explicit data-user-story", result.stderr)

    def test_rejects_user_facing_story_without_binary_declaration(self):
        """prompt-first-shaping #acceptance-guided-declared."""
        base = self.empty_base()
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true">Story</p>')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs data-guided-journey=yes|no", result.stderr)

    def test_rejects_yes_declaration_without_exactly_one_step_link(self):
        """prompt-first-shaping #acceptance-guided-step."""
        base = self.empty_base()
        (self.spec.parent / "guided.spec.html").write_text('<section data-anchor="guided-step">Step</section>\n')
        link = '<a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a>'
        shutil.copy2(FALLBACK, self.style)
        for links in ("", link + link):
            self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes" data-guided-journey-mode="passive">Story ' + links + '</p>')
            result = self.validate(base)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("yes declaration on story story needs exactly one guided-journey step link", result.stderr)

    def test_rejects_yes_declaration_without_passive_or_required_mode(self):
        """prompt-first-shaping #acceptance-guided-mode."""
        base = self.empty_base()
        (self.spec.parent / "guided.spec.html").write_text('<section data-anchor="guided-step">Step</section>\n')
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes">Story <a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a></p>')
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("yes declaration on story story needs passive|required mode", result.stderr)

    def test_validation_reports_alone_and_never_waits_on_jev(self):
        """project-rules #acceptance-light-validate: with no review service, validation reports its result; the validator
        imports no Jev, network, or service module."""
        base = self.empty_base()
        self.write_spec()
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stories=valid", result.stdout)
        tree = ast.parse(VALIDATOR.read_text(encoding="utf-8"))
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse({m for m in modules if m.split(".")[0] in {"socket", "http", "importlib", "asyncio"}
                          or m == "urllib.request"}, modules)
        self.assertNotIn("jev", VALIDATOR.read_text(encoding="utf-8").lower())

    def test_accepts_passive_extension_with_resolved_step_link(self):
        base = self.empty_base()
        journey = self.spec.parent / "guided.spec.html"
        journey.write_text('<section data-anchor="guided-step">Step</section>\n')
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes" data-guided-journey-mode="passive">Story <a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a></p>')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stories=valid", result.stdout)

    def test_required_extension_needs_semantic_success_milestone(self):
        base = self.empty_base()
        journey = self.spec.parent / "guided.spec.html"
        journey.write_text('<section data-anchor="guided-step">Step</section>\n')
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes" data-guided-journey-mode="required">Story <a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a></p>')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs a semantic success milestone", result.stderr)

    def test_rejects_guided_step_link_with_missing_anchor(self):
        base = self.empty_base()
        journey = self.spec.parent / "guided.spec.html"
        journey.write_text('<section data-anchor="other-step">Step</section>\n')
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes" data-guided-journey-mode="required" data-guided-journey-milestone="profile_saved">Story <a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a></p>')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names a missing anchor", result.stderr)

    def test_accepts_required_extension_with_milestone_and_step(self):
        base = self.empty_base()
        journey = self.spec.parent / "guided.spec.html"
        journey.write_text('<section data-anchor="guided-step">Step</section>\n')
        self.write_spec(body='<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="yes" data-guided-journey-mode="required" data-guided-journey-milestone="profile_saved">Story <a data-guided-journey-step href="guided.spec.html#guided-step">Guided step</a></p>')
        shutil.copy2(FALLBACK, self.style)

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stories=valid", result.stdout)

    def test_accepts_marked_spec_with_exact_named_sections(self):
        base = self.empty_base()
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stories=valid", result.stdout)

    def test_rejects_marked_sections_out_of_order(self):
        base = self.empty_base()
        self.write_shaped_spec()
        html = (
            self.spec.read_text()
            .replace('data-spec-section="user-stories"', 'data-spec-section="order-first"', 1)
            .replace('data-spec-section="acceptance"', 'data-spec-section="user-stories"', 1)
            .replace('data-spec-section="order-first"', 'data-spec-section="acceptance"', 1)
        )
        self.spec.write_text(html)
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shaped spec sections must start", result.stderr)

    def test_rejects_marked_sections_nested_out_of_order(self):
        base = self.empty_base()
        self.write_shaped_spec()
        html = self.spec.read_text()
        sections = []
        for anchor in ("user-stories", "acceptance", "modular-boundaries"):
            start = html.index(f'<section data-spec-section="{anchor}"')
            end = html.index("</section>", start) + len("</section>")
            sections.append(html[start:end])
        body_start = html.index('<section data-spec-section="user-stories"')
        body_end = html.index("</article>")
        wrapper = '<section data-anchor="wrapper">\n' + "\n".join(reversed(sections)) + "\n</section>\n"
        self.spec.write_text(html[:body_start] + wrapper + html[body_end:])
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shaped spec sections must start", result.stderr)

    def test_unmarked_legacy_spec_remains_valid_without_section_backfill(self):
        self.write_spec()
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()
        self.write_spec()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_marked_buried_story(self):
        base = self.empty_base()
        story = '<p data-user-story data-anchor="story" data-user-facing="true" data-guided-journey="no">Story</p>'
        self.write_shaped_spec(story=story)
        html = self.spec.read_text().replace('<h2>User stories</h2>'+story, '<h2>User stories</h2>').replace('</article>', story+'</article>')
        self.spec.write_text(html)
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("visibly contained in the User stories section", result.stderr)

    def test_accepts_marked_acceptance_without_optional_scope(self):
        base = self.empty_base()
        self.write_shaped_spec(scope="")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_open_tbd_in_acceptance_criteria(self):
        """repair-specs #acceptance-criteria-unknown: an open TBD commits; the review page gates Accept spec."""
        for scope in ("traceability", ""):
            with self.subTest(scope=scope):
                base = self.empty_base()
                self.write_shaped_spec(scope=scope, acceptance_extra=' data-story="story" data-spec-tbd="open"')
                html = self.spec.read_text().replace('data-anchor="acceptance">', 'data-anchor="acceptance"><p data-spec-tbd="open" data-anchor="acceptance-gap">Missing end state.</p>', 1)
                self.spec.write_text(html)
                shutil.copy2(FALLBACK, self.style)
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_project_rule_mark_needs_a_name_on_an_acceptance_criterion(self):
        """project-rules #declare-text: a mark with an empty name, or on anything but a criterion, is rejected."""
        cases = {
            ' data-story="story" data-project-rule="Every change ships its onboarding"': None,
            ' data-story="story" data-project-rule=""': "project rule mark on acceptance-rule needs a short name",
            ' data-story="story" data-project-rule="  "': "project rule mark on acceptance-rule needs a short name",
        }
        for extra, error in cases.items():
            with self.subTest(extra=extra):
                base = self.empty_base()
                self.write_shaped_spec(acceptance_extra=extra)
                shutil.copy2(FALLBACK, self.style)
                result = self.validate(base)
                if error is None:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(error, result.stderr)
        base = self.empty_base()
        self.write_shaped_spec(boundary_extra=' data-project-rule="Boundaries"')
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("project rule mark on boundary-rule is not on an acceptance criterion", result.stderr)

    def test_rejects_ambiguous_acceptance_scope(self):
        base = self.empty_base()
        self.write_shaped_spec(scope="deferred")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be deferred", result.stderr)

    def test_rejects_marked_noncanonical_acceptance_heading(self):
        base = self.empty_base()
        self.write_shaped_spec(acceptance_heading="Evidence contract acceptance")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("visible Acceptance criteria section", result.stderr)

    def test_rejects_marked_weak_modular_boundary(self):
        base = self.empty_base()
        self.write_shaped_spec(boundary_scope="")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs observable scope", result.stderr)

    def test_rejects_criterion_without_story_claim(self):
        """prompt-first-shaping #acceptance-trace-criterion: no story named."""
        base = self.empty_base()
        self.write_shaped_spec(acceptance_extra="")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acceptance-rule needs data-story", result.stderr)

    def test_rejects_criterion_naming_unknown_story(self):
        """prompt-first-shaping #acceptance-trace-criterion: the named story is not this spec's."""
        base = self.empty_base()
        self.write_shaped_spec(acceptance_extra=' data-story="story missing"')
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names unknown story missing", result.stderr)

    def test_rejects_story_no_criterion_names(self):
        """prompt-first-shaping #acceptance-trace-story."""
        base = self.empty_base()
        extra = '<p data-user-story data-anchor="orphan" data-user-facing="false">Orphan</p>'
        self.write_shaped_spec(story=DEFAULT_STORY + extra)
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("story orphan is named by no acceptance criterion", result.stderr)

    def test_accepts_criterion_naming_several_stories(self):
        base = self.empty_base()
        extra = '<p data-user-story data-anchor="second" data-user-facing="false">Second</p>'
        self.write_shaped_spec(story=DEFAULT_STORY + extra, acceptance_extra=' data-story="story  second"')
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unchanged_legacy_spec_does_not_trigger_structural_backfill(self):
        self.write_spec(body="<p>Legacy outcome</p>")
        self.style.write_text("body { color: #111; }\n")
        base = self.commit()

        result = self.validate(base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stories=unchanged", result.stdout)

    def test_rejects_criterion_scenario_without_when_keyword(self):
        base = self.empty_base()
        self.write_shaped_spec(scenario_text="Given the system is ready")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acceptance-rule scenario needs a When keyword", result.stderr)

    def test_accepts_criterion_scenario_with_given_and_when(self):
        base = self.empty_base()
        self.write_shaped_spec(scenario_text="Given an active session, When the user clicks submit")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_criterion_observable_without_substantive_outcome(self):
        base = self.empty_base()
        self.write_shaped_spec(observable_text="done")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acceptance-rule observable needs a verifiable outcome", result.stderr)

    def test_accepts_criterion_with_well_formed_gwt(self):
        base = self.empty_base()
        self.write_shaped_spec(
            scenario_text="Given a configured environment, When the validator runs",
            observable_text="the report shows all checks passed with no errors",
        )
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)


    def two_screen_warnings(self, result):
        return [line for line in result.stderr.splitlines() if line.startswith("warning:")]

    def test_two_screen_warning_on_added_criteria(self):
        """prompt-first-shaping #complete-criterion-two-screen-warning, #acceptance-one-screen-warn-tabs,
        #acceptance-one-screen-warn-navigation, #acceptance-one-screen-final, #acceptance-one-screen-reason,
        #acceptance-one-screen-no-screen: an added criterion whose Then names two different screens, tabs, pages, or
        routes or joins two outcomes with a navigation step warns once, naming it and the words that raised it; a
        reason, a no-screen Then, focus moving, and a move after only something closing raise none; an empty reason
        still warns; every case passes with the summary line unchanged."""
        tabs = "Then the Transcript tab marks the hook and the Overview tab names it."
        cases = {
            "tabs": (tabs, "", ["Transcript tab", "Overview tab"]),
            "navigation": ("Then the clip plays and Back returns to Discover.", "", ["returns to Discover"]),
            "reason": (tabs, ' data-one-screen="both tabs show side by side on one screen"', None),
            "empty-reason": (tabs, ' data-one-screen=" "', ["Transcript tab", "Overview tab"]),
            "no-screen": ("Then the settings page and the billing page record the change once, proven by a unit test: no screen shows it.", "", None),
            "one-screen": ("Then the Transcript tab marks the hook and names it in its header.", "", None),
            # two different named surfaces of any kind, not only two of one kind
            "page-and-screen": ("Then the Inbox page shows the item and the Board screen lists it.", "", ["Inbox page", "Board screen"]),
            # a no-screen clause naming something else leaves the rest of the Then judged
            "no-screen-other": ("Then the Transcript tab marks the hook and the Overview tab names it, proven by a unit test: no screen shows the log.", "", ["Transcript tab", "Overview tab"]),
            "passive-navigation": ("Then the clip plays and the user is taken back to Discover.", "", ["taken back to Discover"]),
            # focus moving is no navigation
            "focus": ("Then the dialog closes and focus returns to the Save button.", "", None),
            # #acceptance-one-screen-final: only something closing before the move reads from the screen it reaches
            "final-screen": ("Then the menu closes and the user returns to the list.", "", None),
            "final-screen-after-outcome": ("Then the menu closes, the clip plays and Back returns to Discover.", "", ["Back returns to Discover"]),
            # a key pressed, a surface word modifying a noun, and a surface the Then says it does not show name no surface
            "key": ("Then pressing Tab moves focus to the Overview tab.", "", None),
            "modifier": ("Then the settings page shows the user's page title.", "", None),
            # a named surface followed by a noun is still a surface; only a possessive's modifies the noun
            "named-header": ("Then the Board page header shows the count and the Inbox page lists the item.", "", ["Board page", "Inbox page"]),
            "named-content": ("Then the Overview tab content names the hook and the Transcript tab marks it.", "", ["Overview tab", "Transcript tab"]),
            "not-shown": ("Then the card shows the phone's screen and not the whole tall page.", "", None),
        }
        for name, (observable, extra, words) in cases.items():
            with self.subTest(case=name):
                base = self.empty_base()
                self.write_shaped_spec(observable_text=observable, acceptance_extra=' data-story="story"' + extra)
                shutil.copy2(FALLBACK, self.style)
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
                warnings = self.two_screen_warnings(result)
                if words is None:
                    self.assertEqual(warnings, [], result.stderr)
                else:
                    self.assertEqual(len(warnings), 1, result.stderr)
                    self.assertIn("acceptance-rule", warnings[0])
                    for word in words:
                        self.assertIn(word, warnings[0])

    def test_two_screen_warning_skips_criterion_unchanged_from_base(self):
        """prompt-first-shaping #acceptance-one-screen-unedited: editing a different criterion leaves no warning for
        the two-tab criterion the author left as it was; the edited one is still judged."""
        tabs = "Then the Transcript tab marks the hook and the Overview tab names it."
        other = '<p data-acceptance-criterion data-anchor="acceptance-other" data-story="story"><span data-acceptance-scenario>When the author opens it</span><span data-acceptance-observable>{}</span></p>'
        self.write_shaped_spec(observable_text=tabs, extra_criteria=other.format("Then the list shows the item."))
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        self.write_shaped_spec(observable_text=tabs, extra_criteria=other.format("Then the list shows the renamed item."))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.two_screen_warnings(result), [], result.stderr)
        self.write_shaped_spec(observable_text=tabs, extra_criteria=other.format("Then the Inbox page shows the item and the Board page lists it."))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = self.two_screen_warnings(result)
        self.assertEqual(len(warnings), 1, result.stderr)
        self.assertIn("acceptance-other", warnings[0])

    def test_two_screen_warning_skips_criterion_renamed_only_by_anchor(self):
        """prompt-first-shaping #complete-criterion-two-screen-warning: a criterion whose text is unchanged from the
        base raises no warning, also when only its anchor changes."""
        tabs = "Then the Transcript tab marks the hook and the Overview tab names it."
        self.write_shaped_spec(observable_text=tabs)
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        self.spec.write_text(self.spec.read_text().replace('data-anchor="acceptance-rule"', 'data-anchor="acceptance-tabs"'))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.two_screen_warnings(result), [], result.stderr)

    def mechanics_warnings(self, result):
        return [line for line in result.stderr.splitlines() if line.startswith("warning:") and "look or motion mechanics" in line]

    CHOREOGRAPHY = (
        '<table data-anchor="behavior-reveal"><tr><th>Step</th><th>Duration</th></tr>'
        '<tr data-anchor="reveal-fade"><td>The list fades in</td><td>{}</td></tr>'
        '<tr data-anchor="reveal-slide"><td>The panel slides in</td><td>{}</td></tr></table>'
    )

    def test_mechanics_warning_on_added_clauses(self):
        """prompt-first-shaping #complete-mechanics-warning, #tdd-mechanics-warning, #acceptance-mechanics-warn-color,
        #acceptance-mechanics-warn-easing, #acceptance-mechanics-warn-timeline, #acceptance-mechanics-warn-table,
        #acceptance-mechanics-felt: an added clause naming a color value or an easing curve, one holding two durations
        under a second, and an added table holding such a duration in two rows each warn once, naming the clause or
        table and the words that raised it; one duration, a timing of a second or more, and words that only look like
        mechanics raise none; every case passes with the summary line unchanged."""
        cases = {
            "hex": ('<p data-anchor="behavior-save">The Save button is #0a84ff.</p>', "behavior-save", ["#0a84ff"]),
            "rgb": ('<p data-anchor="behavior-save">The Save button is <code>rgb(10, 132, 255)</code>.</p>', "behavior-save", ["rgb(10, 132, 255)"]),
            "hsl": ('<p data-anchor="behavior-save">The Save button is hsl(211 100% 52%).</p>', "behavior-save", ["hsl(211 100% 52%)"]),
            "bezier": ('<p data-anchor="behavior-slide">The panel slides in on cubic-bezier(0.2, 0, 0, 1).</p>', "behavior-slide", ["cubic-bezier(0.2, 0, 0, 1)"]),
            "named-easing": ('<p data-anchor="behavior-slide">The panel slides in with ease-out.</p>', "behavior-slide", ["ease-out"]),
            "linear-easing": ('<p data-anchor="behavior-slide">The panel slides in with a linear easing.</p>', "behavior-slide", ["linear"]),
            "timeline": ('<p data-anchor="behavior-reveal">The list fades in over 200 ms, then the panel slides in over 300ms.</p>', "behavior-reveal", ["200 ms", "300ms"]),
            "seconds": ('<p data-anchor="behavior-reveal">The list fades in over 0.2s, then the panel slides in over two hundred milliseconds.</p>', "behavior-reveal", ["0.2s", "two hundred milliseconds"]),
            "table": (self.CHOREOGRAPHY.format("200 ms", "300 ms"), "table behavior-reveal", ["200 ms", "300 ms"]),
            "quoted-name": ('<p data-anchor="behavior-save">The <q>Save</q> button turns #0a84ff once pressed.</p>', "behavior-save", ["#0a84ff"]),
            # a timing the user feels: one duration in a clause
            "felt": ('<p data-anchor="behavior-answer">An answer shows within 300 ms.</p>', None, None),
            "held": ('<p data-anchor="behavior-press">A press held for 500 milliseconds opens the menu.</p>', None, None),
            "table-one-row": (self.CHOREOGRAPHY.format("200 ms", "after the fade"), None, None),
            "over-a-second": ('<p data-anchor="behavior-reveal">The list shows within two seconds and the report within 1500 ms.</p>', None, None),
            # words that only look like mechanics
            "look-alike": ('<p data-anchor="behavior-history">The history stays linear, the link reads peer-seams#acceptance-ci, and setup is done with ease.</p>', None, None),
            "quoted": ('<p data-anchor="behavior-outcome">State the outcome, never <q>fades in over 200 ms, then slides in over 300 ms</q>.</p>', None, None),
            # a number with thousands separators is one number, a word before parentheses no CSS call, a hash word no color
            "thousands": ('<p data-anchor="behavior-reveal">The list shows within 1,500 ms and the report within 2,000 ms.</p>', None, None),
            "prose-calls": ('<p data-anchor="behavior-labs">The lab(s) on each floor follow the setup steps(1 to 3), kept linear(ish).</p>', None, None),
            "hex-words": ('<p data-anchor="behavior-links">The #add, #cafe, and #fade anchors open the add form, the cafe page, and the fade demo.</p>', None, None),
            # their true cases still warn
            "white": ('<p data-anchor="behavior-page">The page is #fff and its cards #fafafa.</p>', "behavior-page", ["#fff", "#fafafa"]),
            "letter-hex": ('<p data-anchor="behavior-page">The page is #ffeedd, its cards #aabbcc, its rule #abcdef, and its overlay #abcdefaa.</p>', "behavior-page", ["#ffeedd", "#aabbcc", "#abcdef", "#abcdefaa"]),
            "lab": ('<p data-anchor="behavior-save">The Save button is lab(52% 40 -20).</p>', "behavior-save", ["lab(52% 40 -20)"]),
            "steps": ('<p data-anchor="behavior-tick">The counter ticks on steps(4, end).</p>', "behavior-tick", ["steps(4, end)"]),
            "linear-curve": ('<p data-anchor="behavior-slide">The panel slides in on linear(0, 0.25 75%, 1).</p>', "behavior-slide", ["linear(0, 0.25 75%, 1)"]),
        }
        for name, (behavior, label, words) in cases.items():
            with self.subTest(case=name):
                base = self.empty_base()
                self.write_shaped_spec(behavior=behavior)
                shutil.copy2(FALLBACK, self.style)
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
                warnings = self.mechanics_warnings(result)
                if words is None:
                    self.assertEqual(warnings, [], result.stderr)
                else:
                    self.assertEqual(len(warnings), 1, result.stderr)
                    self.assertIn(f"{label} states look or motion mechanics", warnings[0])
                    for word in words:
                        self.assertIn(f'"{word}"', warnings[0])

    def test_mechanics_warning_on_an_added_criterion_naming_a_color(self):
        """prompt-first-shaping #complete-mechanics-warning: a criterion is a clause too; its color warns, naming it."""
        base = self.empty_base()
        self.write_shaped_spec(observable_text="Then the Save button turns #0a84ff.")
        shutil.copy2(FALLBACK, self.style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = self.mechanics_warnings(result)
        self.assertEqual(len(warnings), 1, result.stderr)
        self.assertIn("clause acceptance-rule states", warnings[0])
        self.assertIn('"#0a84ff"', warnings[0])

    def test_mechanics_warning_skips_clause_unchanged_from_base(self):
        """prompt-first-shaping #acceptance-mechanics-unedited: editing a different clause leaves no warning for the
        hex clause or the choreography table the author left as they were, also with an anchor renamed; an edited
        clause is still judged."""
        kept = '<p data-anchor="behavior-save">The Save button is #0a84ff.</p>' + self.CHOREOGRAPHY.format("200 ms", "300 ms")
        other = '<p data-anchor="behavior-other">{}</p>'
        self.write_shaped_spec(behavior=kept + other.format("The list shows the item."))
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        self.write_shaped_spec(behavior=kept.replace("behavior-save", "behavior-saved") + other.format("The list shows the renamed item."))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mechanics_warnings(result), [], result.stderr)
        self.write_shaped_spec(behavior=kept + other.format("The list shows the item in #333."))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = self.mechanics_warnings(result)
        self.assertEqual(len(warnings), 1, result.stderr)
        self.assertIn("clause behavior-other states", warnings[0])

    def test_mechanics_warning_counts_only_the_table_rows_a_change_adds_or_edits(self):
        """prompt-first-shaping #complete-mechanics-warning, #acceptance-mechanics-unedited: a table whose timeline rows
        are as the base had them raises no warning when the change edits another of its cells, or one timeline row; two
        rows the change adds, each holding a duration, warn, naming only theirs."""
        def table(*rows):
            return ('<table data-anchor="behavior-reveal"><tr><th>Step</th><th>Duration</th></tr>'
                    + "".join(f"<tr><td>{step}</td><td>{duration}</td></tr>" for step, duration in rows) + "</table>")

        kept = [("The list fades in", "200 ms"), ("The panel slides in", "300 ms"), ("The title shows", "after the slide")]
        self.write_shaped_spec(behavior=table(*kept))
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        for name, rows, words in (
            ("other cell", kept[:2] + [("The title shows", "once the panel is in")], None),
            ("one timeline row", [("The list fades in", "250 ms")] + kept[1:], None),
            ("two rows added", kept + [("The chart draws", "400 ms"), ("The legend shows", "500 ms")], ["400 ms", "500 ms"]),
        ):
            with self.subTest(change=name):
                self.write_shaped_spec(behavior=table(*rows))
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                warnings = self.mechanics_warnings(result)
                if words is None:
                    self.assertEqual(warnings, [], result.stderr)
                else:
                    self.assertEqual(len(warnings), 1, result.stderr)
                    self.assertIn('table behavior-reveal states look or motion mechanics ("400 ms", "500 ms")', warnings[0])

    OTHER = ('<p data-acceptance-criterion data-anchor="acceptance-other" data-story="story">'
             '<span data-acceptance-scenario>When the author opens it</span><span data-acceptance-observable>{}</span></p>')

    def test_markup_checks_skip_criterion_unchanged_from_base(self):
        """prompt-first-shaping #complete-criterion-judged, #acceptance-validate-unedited: the scenario and observable
        checks run only on criteria the change adds or edits. A criterion that misses one passes while the author edits
        a different criterion, also with its anchor renamed; once the author edits that criterion, it fails."""
        cases = {
            "no-when": ({"scenario_text": "Given the system is ready"}, {"scenario_text": "Given the system is ready now"},
                        "acceptance-rule scenario needs a When keyword"),
            "short-observable": ({"observable_text": "done"}, {"observable_text": "finished"},
                                 "acceptance-rule observable needs a verifiable outcome"),
            "no-observable": ({"observable_text": ""}, {"observable_text": "", "scenario_text": "When the rule is exercised again"},
                              "acceptance-rule needs an observable outcome"),
            "no-scenario": ({"scenario_text": ""}, {"scenario_text": "", "observable_text": "The result is recorded again"},
                            "acceptance-rule needs an observable scenario"),
        }
        for name, (unedited, edited, error) in cases.items():
            with self.subTest(case=name):
                self.write_shaped_spec(extra_criteria=self.OTHER.format("Then the list shows the item."), **unedited)
                shutil.copy2(FALLBACK, self.style)
                base = self.commit()
                self.write_shaped_spec(extra_criteria=self.OTHER.format("Then the list shows the renamed item."), **unedited)
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
                self.spec.write_text(self.spec.read_text().replace('data-anchor="acceptance-rule"', 'data-anchor="acceptance-kept"'))
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.write_shaped_spec(extra_criteria=self.OTHER.format("Then the list shows the renamed item."), **edited)
                result = self.validate(base)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_rejects_change_dropping_a_proof_clause_the_base_carries(self):
        """prompt-first-shaping #complete-criterion-judged, #acceptance-validate-proof-dropped,
        #acceptance-proof-clause-rule: each proof clause a base criterion carries reads word for word in a criterion of
        the changed spec, or validation fails naming that base criterion and the clause. Keeping it while rewriting the
        rest, renaming the anchor, or splitting the criterion passes, and so does deleting or stripping a criterion whose
        clause another criterion still carries; a clause the criterion only quotes is no clause it carries."""
        clause = "proven by a unit test: no screen shows it"
        workflow = "proven by a unit test that reads the workflow file: no screen shows it"
        once, plain = "Then the event is recorded once", "Then the list shows the item."
        # name: (base criteria, changed criteria, anchor renamed, base criterion and clause the failure names or None)
        cases = {
            "dropped": ((f"{once}, {clause}.", None), (f"{once}.", None), False, ("acceptance-rule", clause)),
            "dropped-other-clause": ((f"{once}, {workflow}.", None), (f"{once}.", None), False, ("acceptance-rule", workflow)),
            "criterion-removed": ((plain, f"{once}, {clause}."), (plain, None), False, ("acceptance-other", clause)),
            # a clause another criterion of the changed spec still carries is not dropped, whichever criterion lost it
            "removed-clause-carried-elsewhere": ((f"{once}, {clause}.", f"Then the list shows the item, {clause}."),
                                                 (f"{once}, {clause}.", None), False, None),
            "stripped-clause-carried-elsewhere": ((f"{once}, {clause}.", f"Then the log names the item, {clause}."),
                                                  (f"{once}.", f"Then the log names the item, {clause}."), False, None),
            "kept-rewritten": ((f"{once}, {clause}.", None), (f"Then the event is recorded twice, {clause}.", None), False, None),
            "anchor-renamed": ((f"{once}, {clause}.", plain), (f"{once}, {clause}.", "Then the list shows the renamed item."), True, None),
            "split": ((f"{once} and the log names it, {clause}.", None), (f"{once}, {clause}.", "Then the log names the event."), False, None),
            "quoted": (("Then the panel reads <q>proven by a unit test: no screen shows it</q> beside it.", None),
                       ("Then the panel reads the clause beside it.", None), False, None),
        }

        def write(rule, other):
            self.write_shaped_spec(observable_text=rule, extra_criteria=self.OTHER.format(other) if other else "")

        for name, (before, after, renamed, dropped) in cases.items():
            with self.subTest(case=name):
                write(*before)
                shutil.copy2(FALLBACK, self.style)
                base = self.commit()
                write(*after)
                if renamed:
                    self.spec.write_text(self.spec.read_text().replace('data-anchor="acceptance-rule"', 'data-anchor="acceptance-event"'))
                result = self.validate(base)
                if dropped is None:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('criterion %s at the base carries the proof clause "%s"' % dropped, result.stderr)
                    self.assertEqual(result.stderr.count("at the base carries"), 1, result.stderr)

    # mockups #checks-validator: the validator checks the mockup set of each spec it validates.
    def mockup_set(self):
        return self.spec.parent / "mockups" / "example"

    def design_source(self):
        """The repo's design source, committed once; a fidelity note names it at that commit."""
        if not hasattr(self, "design_commit"):
            (self.repo / "DESIGN.md").write_text("# Design\n--ui-ink: #333333\n")
            self.git("add", "DESIGN.md")
            self.git("commit", "-qm", "design source")
            self.design_commit = self.git("rev-parse", "HEAD").stdout.strip()
        return f"DESIGN.md@{self.design_commit}"

    def write_mockup(self, name="01-host-page.mockup.html", frames=None, style=MOCKUP_STYLE, note="design", head="", after="", runtime="../../../.viz/runtime.js", chrome="Northwind CRM"):
        """A drawn mockup at production fidelity unless an argument takes a part of it away: it draws the chrome of a
        product the project does not own, chrome naming it (mockups #markup-chrome), and loads the runtime its spec page
        loads, docs/specs/.viz/runtime.js, from the set's folder, or no runtime when runtime is None."""
        if frames is None:
            frames = DEFAULT_FRAMES
        if note == "design":
            note = self.design_source()
        meta = f'<meta name="mockup-chrome" content="{chrome}">' if chrome is not None else ""
        meta += f'<meta name="mockup-design-source" content="{note}">' if note is not None else ""
        script = f'<script defer src="{runtime}"></script>' if runtime else ""
        folder = self.mockup_set()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(
            f'<!doctype html><html><head><title>Host page - mockup</title>{meta}<style>{style}</style>{head}{script}</head>'
            f"<body>{frames}{after}</body></html>\n"
        )
        return path.relative_to(self.repo).as_posix()

    def write_prototype(self, name="01-report-page.mockup.html", body=PROTOTYPE_BODY):
        """A prototype mockup: its route, regions, steps with their actions, and questions; no frame, no style."""
        folder = self.mockup_set()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(
            '<!doctype html><html><head><title>Report page - mockup</title>'
            f'<meta name="mockup-prototype" content="/reports/weekly"></head><body>{body}</body></html>\n'
        )
        return path.relative_to(self.repo).as_posix()

    def edited_mockup(self, **page):
        """A well-formed mockup committed at the base, then edited by the change; returns (base, mockup)."""
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        self.write_mockup()
        base = self.commit()
        return base, self.write_mockup(**page)

    def assertFailsNaming(self, result, mockup, *words, problems=None):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(mockup, result.stderr)
        for words_part in words:
            self.assertIn(words_part, result.stderr)
        if problems is not None:
            self.assertEqual(result.stderr.count(mockup), problems, result.stderr)

    def shaped_base(self):
        base = self.empty_base()
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        return base

    def test_accepts_a_well_formed_mockup_set(self):
        """mockups #set, #frames: two screens and an image beside them pass with the summary line unchanged."""
        base = self.shaped_base()
        self.write_mockup()
        self.write_mockup("02-thread-on-phone.mockup.html")
        (self.mockup_set() / "avatar.png").write_bytes(b"\x89PNG\r\n")
        (self.mockup_set() / "01-host-page.mockup.html.review").mkdir()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        self.assertNotIn("mockup", result.stderr)

    def test_rejects_mockup_naming_an_anchor_the_spec_lacks(self):
        """mockups #acceptance-validate-anchor: names the mockup and the missing anchor."""
        base = self.shaped_base()
        mockup = self.write_mockup(frames=(
            '<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-rule acceptance-gone"></section>'
            '<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-rule"></section>'
        ))
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(mockup, result.stderr)
        self.assertIn("acceptance-gone", result.stderr)
        self.assertNotIn("acceptance-rule ", result.stderr)

    def test_rejects_a_step_illustrating_an_anchor_the_spec_lacks(self):
        """mockups #illustrates-rule, #markup-illustrates: a step names spec anchors it illustrates as a part does; one
        the spec lacks fails, naming the mockup and the anchor, and one the spec holds passes."""
        base = self.shaped_base()
        steps = STEPS.replace('data-step="team-open"', 'data-step="team-open" data-illustrates="acceptance-rule acceptance-gone"')
        mockup = self.write_mockup(after=steps)
        self.assertFailsNaming(self.validate(base), mockup, "acceptance-gone", problems=1)
        self.write_mockup(after=steps.replace(" acceptance-gone", ""))
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_mockup_with_a_desktop_frame_and_no_phone_frame(self):
        """mockups #acceptance-validate-frames: names the mockup and the missing phone frame."""
        base = self.shaped_base()
        mockup = self.write_mockup(frames=(
            '<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-rule"></section>'
        ))
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(mockup, result.stderr)
        self.assertIn("no phone frame", result.stderr)

    def test_rejects_each_mockup_set_problem_naming_the_mockup(self):
        """mockups #checks-validator: each file's name, exactly one desktop and one phone frame, unique anchors,
        every frame naming an illustrated anchor; each problem fails, naming the mockup and what is wrong."""
        ok_phone = '<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-rule"></section>'
        ok_desktop = '<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-rule"></section>'
        cases = {
            "name": ("1-Host Page.mockup.html", ok_desktop + ok_phone, "name"),
            "no-desktop": ("01-host.mockup.html", ok_phone, "no desktop frame"),
            "two-phone": ("01-host.mockup.html", ok_desktop + ok_phone + ok_phone.replace('data-anchor="phone"', 'data-anchor="phone-2"'), "2 phone frames"),
            "unknown-frame": ("01-host.mockup.html", ok_desktop + ok_phone + '<section data-mockup-frame="tablet" data-anchor="tablet" data-illustrates="acceptance-rule"></section>', "tablet"),
            "duplicate-anchor": ("01-host.mockup.html", ok_desktop.replace("></section>", '><div data-anchor="card"></div><div data-anchor="card"></div></section>') + ok_phone, "card"),
            "frame-illustrates-nothing": ("01-host.mockup.html", ok_desktop + '<section data-mockup-frame="phone" data-anchor="phone"><div data-anchor="card"></div></section>', "phone frame"),
            # #illustrates-rule: only a frame or a part with its own data-anchor names what it shows, as the service reads it.
            "illustrates-on-an-unanchored-part": ("01-host.mockup.html", ok_desktop + '<section data-mockup-frame="phone" data-anchor="phone"><div data-illustrates="acceptance-rule"></div></section>', "phone frame"),
        }
        for case, (name, frames, words) in cases.items():
            with self.subTest(case=case):
                base = self.shaped_base()
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                mockup = self.write_mockup(name, frames)
                result = self.validate(base)
                self.assertNotEqual(result.returncode, 0, case)
                self.assertIn(mockup, result.stderr)
                self.assertIn(words, result.stderr)

    def test_checks_the_mockup_set_of_an_unchanged_spec(self):
        """mockups #checks-validator: a set changed under an unchanged spec is still checked."""
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        self.write_mockup()
        base = self.commit()
        self.assertEqual(self.validate(base).returncode, 0)
        mockup = self.write_mockup(frames='<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-rule"></section>')
        result = self.validate(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(mockup, result.stderr)
        self.assertIn("no phone frame", result.stderr)

    # mockups #checks-fidelity, #checks-warn: on each mockup the change adds or edits.
    def test_accepts_a_prototype_mockup_with_no_frame_and_no_fidelity_note(self):
        """mockups #prototype-file, #check-route: a route names the screen; no frame, note, or style is needed."""
        base = self.shaped_base()
        self.write_prototype()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        self.assertNotIn("mockup", result.stderr)

    def test_rejects_added_mockup_naming_no_route_and_drawing_no_frame(self):
        """mockups #acceptance-validate-route: names the mockup and the missing route."""
        base = self.shaped_base()
        mockup = self.write_mockup(frames='<main data-anchor="screen" data-illustrates="acceptance-rule">Weekly report</main>')
        result = self.validate(base)
        self.assertFailsNaming(result, mockup, "missing route", problems=1)
        self.assertNotIn("no desktop frame", result.stderr)

    def test_rejects_added_drawn_mockup_with_no_fidelity_note(self):
        """mockups #acceptance-validate-source: names the mockup and the missing design source."""
        base = self.shaped_base()
        mockup = self.write_mockup(note=None)
        self.assertFailsNaming(self.validate(base), mockup, "no fidelity note", "missing design source", problems=1)

    def test_rejects_added_drawn_mockup_naming_no_chrome(self):
        """mockups #acceptance-validate-chrome, #check-chrome: a drawn mockup naming no product whose chrome it draws,
        or an empty name, fails, naming the mockup and that a screen the project owns is its running prototype."""
        for chrome in (None, " "):
            with self.subTest(chrome=chrome):
                base = self.shaped_base()
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                mockup = self.write_mockup(chrome=chrome)
                result = self.validate(base)
                self.assertFailsNaming(result, mockup, "names no product whose chrome it draws", "a screen the project owns is its running prototype", problems=1)
                self.assertNotIn("warning", result.stderr)

    def test_rejects_edited_mockup_coloring_a_button_with_no_token(self):
        """mockups #acceptance-validate-color: names the mockup and that color."""
        base, mockup = self.edited_mockup(style=MOCKUP_STYLE + " [data-mockup-frame] button { background: #e11d48; }")
        self.assertFailsNaming(self.validate(base), mockup, "color #e11d48", problems=1)

    def test_rejects_edited_mockup_moving_a_panel_with_no_token_duration(self):
        """mockups #acceptance-validate-motion: names the mockup and that duration."""
        base, mockup = self.edited_mockup(style=MOCKUP_STYLE + " [data-mockup-frame] .panel { transition: transform 240ms var(--ui-motion-ease); }")
        self.assertFailsNaming(self.validate(base), mockup, "duration 240ms", problems=1)

    def test_mockup_token_check_reads_durations_unchanged_by_the_mechanics_warning(self):
        """mockups #check-tokens: a duration inside a token's name and a number apart from any unit state no duration,
        so the mockup passes; the clause mechanics warning's own duration reading never reaches the token check."""
        style = MOCKUP_STYLE + (" [data-mockup-frame] .panel { opacity: var(--motion-200ms);"
                                " transition: transform 1 s var(--ui-motion-ease); }")
        base, mockup = self.edited_mockup(style=style)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("duration", result.stderr)

    def test_rejects_edited_mockup_moving_with_no_reduced_motion_rule(self):
        """mockups #acceptance-validate-reduced: names the mockup and the missing reduced-motion rule."""
        style = MOCKUP_STYLE.replace(REDUCED, "") + " [data-mockup-frame] .panel { transition: transform var(--ui-motion-duration) var(--ui-motion-ease); }"
        base, mockup = self.edited_mockup(style=style)
        self.assertFailsNaming(self.validate(base), mockup, "missing @media (prefers-reduced-motion)", problems=1)

    def test_rejects_edited_mockup_holding_a_button_and_no_focus_style(self):
        """mockups #acceptance-validate-focus: names the mockup and the missing focus state."""
        base, mockup = self.edited_mockup(style=MOCKUP_STYLE.replace(FOCUS, ""))
        self.assertFailsNaming(self.validate(base), mockup, "phone frame holds a button, link, or field", "missing focus state", problems=1)

    def test_rejects_edited_mockup_holding_a_disabled_button_and_no_disabled_style(self):
        """mockups #acceptance-validate-disabled: names the mockup and the missing disabled state."""
        frames = DEFAULT_FRAMES.replace("<button>Save</button>", "<button disabled>Save</button>")
        base, mockup = self.edited_mockup(frames=frames, style=MOCKUP_STYLE.replace(DISABLED, ""))
        self.assertFailsNaming(self.validate(base), mockup, "phone frame holds a disabled control", "missing disabled state", problems=1)

    def test_rejects_edited_mockup_loading_a_font_file_the_repo_lacks(self):
        """mockups #acceptance-validate-font: names the mockup and the missing font file; kept beside it, it passes."""
        face = ' @font-face { font-family: "Inter Variable"; src: url("InterVariable.woff2") format("woff2"); }'
        base, mockup = self.edited_mockup(style=MOCKUP_STYLE + face)
        self.assertFailsNaming(self.validate(base), mockup, "font file InterVariable.woff2 is not in the repo", problems=1)
        (self.mockup_set() / "InterVariable.woff2").write_bytes(b"wOF2")
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(mockup, result.stderr)

    def test_rejects_mockup_asking_a_question_at_a_step_its_walkthrough_lacks(self):
        """mockups #acceptance-validate-question-step: names the mockup, the question, and the missing step."""
        base = self.shaped_base()
        mockup = self.write_mockup(after=STEPS + question("export"))
        self.assertFailsNaming(self.validate(base), mockup, "question question-trend", 'step "export"', problems=1)

    def test_rejects_edited_mockup_naming_a_changed_design_source(self):
        """mockups #acceptance-validate-drift, #check-source-moved: fails, naming the mockup and that design source, and
        no longer warns on it; the mockup untouched passes as it is."""
        base, mockup = self.edited_mockup(after=STEPS)
        (self.repo / "DESIGN.md").write_text("# Design\n--ui-ink: #222222\n")
        result = self.validate(base)
        self.assertFailsNaming(result, mockup, f"design source DESIGN.md changed since {self.design_commit}", problems=1)
        self.assertNotIn("warning", result.stderr)
        self.write_mockup()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(mockup, result.stderr)

    def test_warns_and_passes_on_edited_mockup_whose_fidelity_note_reads_none(self):
        """mockups #checks-warn: a note reading none warns and passes."""
        base, mockup = self.edited_mockup(note="none")
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"warning: mockup {mockup}: fidelity note reads none", result.stderr)

    def test_passes_an_untouched_mockup_with_no_fidelity_note(self):
        """mockups #acceptance-validate-unchanged: a mockup the change leaves untouched passes as it is."""
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        mockup = self.write_mockup(note=None, style="", head="<script>window.legacy = 1</script>", chrome=None)
        base = self.commit()
        self.write_shaped_spec(observable_text="The observable result is recorded on the report")
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        self.assertNotIn(mockup, result.stderr)

    def test_rejects_each_fidelity_and_walkthrough_problem_naming_the_mockup(self):
        """mockups #checks-fidelity: each row fails on an added mockup, naming the mockup and what is wrong."""
        frames_with = lambda extra: DEFAULT_FRAMES.replace("<button>Save</button>", f"<button>Save</button>{extra}")
        cases = {
            "both-kinds": ({"head": '<meta name="mockup-prototype" content="/reports/weekly">'}, "a prototype mockup draws no frame"),
            "note-file-missing": ({"note": "docs/DESIGN.md@HEAD"}, "design source docs/DESIGN.md, which is not in the repo"),
            "note-entry-malformed": ({"note": "DESIGN.md"}, '"DESIGN.md" must be <path>@<commit>'),
            "note-unknown-commit": ({"note": "DESIGN.md@0000000"}, "a commit Git cannot find"),
            "easing-function": ({"style": MOCKUP_STYLE + " .panel { transition: opacity var(--ui-motion-duration) cubic-bezier(0.2, 0, 0, 1); }"}, "easing cubic-bezier(0.2, 0, 0, 1)"),
            "easing-name": ({"style": MOCKUP_STYLE + " .panel { transition: opacity var(--ui-motion-duration) ease-in-out; }"}, "easing ease-in-out"),
            "named-color": ({"style": MOCKUP_STYLE + " .card { border: 1px solid tomato; }"}, "color tomato"),
            "color-function": ({"style": MOCKUP_STYLE + " .card { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); }"}, "color rgba(0, 0, 0, 0.2)"),
            "token-fallback-color": ({"style": MOCKUP_STYLE + " .card { color: var(--ui-ink, #000); }"}, "color #000"),
            "inline-style-color": ({"frames": frames_with('<p style="color: #ff0000">Overdue</p>')}, "color #ff0000"),
            "svg-paint": ({"frames": frames_with('<svg viewBox="0 0 8 8"><path fill="#00ff00" d="M0 0h8v8z"/></svg>')}, "color #00ff00"),
            "font-not-from-token": ({"style": MOCKUP_STYLE + " .card { font-family: Georgia, serif; }"}, "font Georgia, serif does not come from a token"),
            "font-shorthand-not-from-token": ({"style": MOCKUP_STYLE + " .card { font: 600 12px var(--ui-font), serif; }"}, "font 600 12px var(--ui-font), serif"),
            "no-font": ({"style": MOCKUP_STYLE.replace(FONT, "")}, "frames take no font from a token"),
            "remote-font": ({"style": MOCKUP_STYLE + " @font-face { font-family: Inter; src: url(https://fonts.example/inter.woff2); }"}, "font file https://fonts.example/inter.woff2"),
            "focus-on-the-other-frame": ({"style": MOCKUP_STYLE.replace("[data-mockup-frame] button:focus-visible", '[data-mockup-frame="desktop"] button:focus-visible')}, "phone frame holds a button, link, or field"),
            "link": ({"frames": DEFAULT_FRAMES.replace('alt="Ada">', 'alt="Ada"><a href="#team">Payments</a>'), "style": MOCKUP_STYLE.replace(FOCUS, " [data-mockup-frame=phone] button:focus { outline: 2px solid var(--ui-action); }")}, "desktop frame holds a button, link, or field"),
            "field": ({"frames": DEFAULT_FRAMES.replace('alt="Ada">', 'alt="Ada"><input value="Payments">'), "style": MOCKUP_STYLE.replace(FOCUS, " [data-mockup-frame=phone] button:focus { outline: 2px solid var(--ui-action); }")}, "desktop frame holds a button, link, or field"),
            "aria-disabled": ({"frames": DEFAULT_FRAMES.replace("<button>", '<button aria-disabled="true">')}, "[aria-disabled] style, the missing disabled state"),
            "keyframes": ({"style": MOCKUP_STYLE.replace(REDUCED, "").replace(MOTION, "") + " @keyframes pulse { from { opacity: 0; } }"}, "styles move (@keyframes) and hold no reduced-motion rule"),
            "inline-script": ({"head": "<script>window.step = 1</script>"}, "script (inline) is not the page runtime"),
            "other-script": ({"head": '<script src="app.js"></script>'}, "script app.js is not the page runtime"),
            "handler": ({"frames": DEFAULT_FRAMES.replace("<button>", '<button onclick="go()">')}, "script in onclick on <button>"),
            "duplicate-step": ({"after": STEPS.replace('data-step="team-open"', 'data-step="totals"')}, 'step "totals" named more than once'),
            # data-at lists the steps reached separated by spaces (#markup-at), so a step's name is one word.
            "step-name-with-a-space": ({"after": STEPS.replace('data-step="team-open"', 'data-step="team open"')}, 'step "team open" holds a space'),
            "step-names-a-missing-part": ({"after": STEPS.replace('data-step-by="desktop-card"', 'data-step-by="export-button"')}, 'step "team-open" names part export-button, which the mockup lacks'),
            "question-names-a-missing-part": ({"after": STEPS + question("team-open", about="chart")}, "question question-trend names part chart, which the mockup lacks"),
        }
        for case, (page, words) in cases.items():
            with self.subTest(case=case):
                base = self.shaped_base()
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                mockup = self.write_mockup(**page)
                self.assertFailsNaming(self.validate(base), mockup, words)

    def test_accepts_a_drawn_mockup_loading_the_runtime_its_spec_page_loads(self):
        """mockups #set-self-contained, #check-script: the script passes when it resolves to the runtime the spec page's
        own runtime script loads, for a spec one folder below the spec folder and for one in it, and decoded as a browser
        decodes a percent-encoded src, the file preflight migrates (tests/review-surface-preflight.py)."""
        layouts = {
            "nested": ("docs/specs/domains/example.spec.html", "../.style/spec.css", "../.viz/runtime.js", "../../../.viz/runtime.js"),
            "flat": ("docs/specs/example.spec.html", ".style/spec.css", "./.viz/runtime.js", "../../.viz/runtime.js"),
            "encoded": ("docs/specs/example.spec.html", ".style/spec.css", "./spec%20viz/runtime.js", "../../spec viz/runtime.js"),
        }
        for layout, (spec, href, runtime, loaded) in layouts.items():
            with self.subTest(layout=layout):
                self.spec = self.repo / spec
                base = self.empty_base()
                self.write_shaped_spec(href=href, runtime=runtime)
                shutil.copy2(FALLBACK, self.style)
                self.write_mockup(runtime=loaded)
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("mockup", result.stderr)

    def test_rejects_a_drawn_mockup_script_other_than_its_spec_pages_runtime(self):
        """mockups #check-script: a runtime.js beside the spec, not the one its page loads, fails, naming the mockup and
        the script, though the file is there."""
        base = self.shaped_base()
        beside = self.spec.parent / ".viz" / "runtime.js"
        beside.parent.mkdir()
        beside.write_text("// a runtime the spec page does not load\n")
        mockup = self.write_mockup(runtime="../../.viz/runtime.js")
        self.assertFailsNaming(self.validate(base), mockup, "script ../../.viz/runtime.js is not the page runtime", problems=1)

    def test_rejects_a_drawn_mockup_script_when_its_spec_page_loads_no_runtime(self):
        """mockups #check-script: a spec page loading no runtime.js has no page runtime, so a drawn mockup's script fails,
        naming the mockup, the script, and that the spec page loads none; a mockup with no script passes."""
        base = self.empty_base()
        self.write_shaped_spec(runtime=None)
        shutil.copy2(FALLBACK, self.style)
        mockup = self.write_mockup()
        self.assertFailsNaming(self.validate(base), mockup, "script ../../../.viz/runtime.js is not the page runtime", "the spec page loads no single runtime.js", problems=1)
        self.write_mockup(runtime=None)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_names_the_missing_page_runtime_only_on_a_relative_script(self):
        """mockups #check-script: with no page runtime, an inline, remote, or root-relative script fails as not the page
        runtime without the words that the spec page loads none, which only a relative src gets."""
        base = self.empty_base()
        self.write_shaped_spec(runtime=None)
        shutil.copy2(FALLBACK, self.style)
        scripts = {
            "inline": ({"runtime": None, "head": "<script>window.step = 1</script>"}, "script (inline)"),
            "remote": ({"runtime": "https://example.test/runtime.js"}, "script https://example.test/runtime.js"),
            "root-relative": ({"runtime": "/docs/specs/.viz/runtime.js"}, "script /docs/specs/.viz/runtime.js"),
        }
        for kind, (page, named) in scripts.items():
            with self.subTest(kind=kind):
                mockup = self.write_mockup(**page)
                result = self.validate(base)
                self.assertFailsNaming(result, mockup, f"{named} is not the page runtime, a mockup carries", problems=1)
                self.assertNotIn("loads no single runtime.js", result.stderr)

    def test_checks_the_walkthrough_of_a_prototype_mockup(self):
        """mockups #check-steps, #check-question-step: the last two rows on a prototype mockup too."""
        base = self.shaped_base()
        mockup = self.write_prototype(body=PROTOTYPE_BODY.replace('data-step="team-open">Should', 'data-step="export">Should'))
        self.assertFailsNaming(self.validate(base), mockup, 'step "export", which its walkthrough lacks', problems=1)

    def test_accepts_mockups_showing_their_directions(self):
        """mockups #check-directions, #frame-viewports: a prototype mockup naming two directions, the other by its route,
        and a drawn one naming three, each other one by its own desktop and phone frame, pass."""
        base = self.shaped_base()
        self.write_prototype(body=directions(PICK, ROUTED) + PROTOTYPE_BODY)
        lists = CARDS.replace('"cards"', '"list"').replace(">Cards<", ">List<")
        list_frames = CARDS_FRAMES.replace('"cards', '"list')
        self.write_mockup("02-host-page.mockup.html", frames=directions(PICK, CARDS, lists) + DEFAULT_FRAMES + CARDS_FRAMES + list_frames)
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        self.assertNotIn("mockup", result.stderr)

    def test_rejects_edited_mockup_holding_two_directions_and_no_pick(self):
        """mockups #acceptance-validate-directions: names the mockup and the missing pick; untouched by the change it
        passes as it is, and the whole-spec check fails it."""
        unpicked = PICK.replace(' data-pick="Totals compare at a glance"', "")
        for kind, (write, page) in {
            "prototype": (self.write_prototype, {"body": directions(unpicked, ROUTED) + PROTOTYPE_BODY}),
            "drawn": (self.write_mockup, {"frames": directions(unpicked, CARDS) + DEFAULT_FRAMES + CARDS_FRAMES}),
        }.items():
            with self.subTest(kind=kind):
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                self.write_shaped_spec()
                shutil.copy2(FALLBACK, self.style)
                write()
                base = self.commit()
                mockup = write(**page)
                self.assertFailsNaming(self.validate(base), mockup, "directions list marks no pick, the missing pick", problems=1)
                base = self.commit()
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(mockup, result.stderr)
                self.assertFailsNaming(self.validate(base, whole_spec=True), mockup, "the missing pick", problems=1)

    def test_rejects_each_directions_problem_naming_the_mockup(self):
        """mockups #check-directions: two or three directions with unique names, exactly one the pick with its reason,
        each with its gives-up line, each other one its route or its two frames naming it, and no frame naming the pick
        or a direction the list lacks; each problem fails on an added mockup, naming the mockup, the direction, and what
        it lacks; one list, its own anchor, and no frame's data-direction empty."""
        pick_frames = CARDS_FRAMES.replace('"cards', '"table')
        other = lambda key, name, item=ROUTED: item.replace('"cards"', f'"{key}"').replace(">Cards<", f">{name}<")
        prototype = lambda *items: (self.write_prototype, {"body": "".join(items) + PROTOTYPE_BODY})
        drawn = lambda *parts: (self.write_mockup, {"frames": "".join(parts) + DEFAULT_FRAMES})
        cases = {
            "one-direction": (prototype(directions(PICK)), "directions list holds 1 direction, needs two or three"),
            "four-directions": (prototype(directions(PICK, ROUTED, other("list", "List"), other("chart", "Chart"))), "holds 4 directions, needs two or three"),
            "two-picks": (prototype(directions(PICK, ROUTED.replace("<li ", '<li data-pick="Bigger numbers" '))), 'marks 2 picks ("Table", "Cards"), needs exactly one'),
            "pick-with-no-reason": (prototype(directions(PICK.replace("Totals compare at a glance", ""), ROUTED)), 'direction "Table" lacks its reason in data-pick'),
            "pick-with-a-bare-reason": (prototype(directions(PICK.replace('="Totals compare at a glance"', ""), ROUTED)), 'direction "Table" lacks its reason in data-pick'),
            "same-name": (prototype(directions(PICK, ROUTED, other("tiles", "Cards"))), 'direction name "Cards" used more than once'),
            "same-key": (prototype(directions(PICK, ROUTED, other("cards", "Tiles"))), 'direction key "cards" used more than once'),
            "no-name": (prototype(directions(PICK, other("cards", ""))), 'direction "cards" lacks its short name, as its text'),
            "no-key": (prototype(directions(PICK, other("", "Cards"))), 'direction "Cards" lacks its key in data-direction'),
            "no-gives-up": (prototype(directions(PICK, ROUTED.replace(' data-gives-up="Fewer teams fit above the fold"', ""))), 'direction "Cards" lacks its gives-up line in data-gives-up'),
            "prototype-direction-with-no-route": (prototype(directions(PICK, CARDS)), 'direction "Cards" lacks its route in data-route'),
            "drawn-direction-with-no-frames": (drawn(directions(PICK, CARDS)), 'direction "Cards" lacks its desktop and phone frames, each naming it in data-direction="cards"'),
            "frames-naming-the-pick": (drawn(directions(PICK, CARDS), CARDS_FRAMES, pick_frames), 'frames name direction "table", the pick, whose frames name no direction'),
            "frames-naming-a-direction-the-list-lacks": (drawn(directions(PICK, CARDS), CARDS_FRAMES, CARDS_FRAMES.replace('"cards', '"list')), 'frames name direction "list", which the directions list lacks'),
            "frames-naming-a-direction-and-no-list": (drawn(CARDS_FRAMES), 'frames name direction "cards", and the mockup holds no directions list naming it'),
            "two-lists": (prototype(directions(PICK, ROUTED), directions(PICK, ROUTED).replace('data-anchor="directions"', 'data-anchor="directions-2"')), "2 directions lists, a mockup names its directions in one"),
            "list-with-no-anchor": (prototype(directions(PICK, ROUTED).replace(' data-anchor="directions"', "")), "directions list lacks its own anchor in data-anchor, where a Pick's answer is saved"),
            "frame-with-an-empty-direction": (drawn(directions(PICK, CARDS), CARDS_FRAMES, CARDS_FRAMES.split("</section>")[0].replace('"cards"', '" "').replace('"cards-desktop"', '"loose-desktop"') + "</section>"),
                                              "desktop frame names no direction in its empty data-direction"),
            "frame-with-a-bare-direction": (drawn(directions(PICK, CARDS), CARDS_FRAMES, CARDS_FRAMES.split("</section>")[0].replace('="cards"', "").replace('"cards-desktop"', '"loose-desktop"') + "</section>"),
                                            "desktop frame names no direction in its empty data-direction"),
        }
        for case, ((write, page), words) in cases.items():
            with self.subTest(case=case):
                base = self.shaped_base()
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                mockup = write(**page)
                self.assertFailsNaming(self.validate(base), mockup, words, problems=1)

    def test_rejects_a_direction_without_exactly_one_desktop_and_one_phone_frame(self):
        """mockups #checks-validator, #frame-viewports, #markup-frame: a drawn mockup holds exactly one desktop and one
        phone frame for its screen, naming no direction, and for each other direction it draws, naming it, each naming an
        anchor it illustrates; each problem fails, naming the mockup, the direction, and the frame."""
        desktop, phone = CARDS_FRAMES.split("</section>")[:2]
        cases = {
            "no-phone-frame": (DEFAULT_FRAMES + desktop + "</section>", ['no phone frame of direction "cards"']),
            "two-desktop-frames": (DEFAULT_FRAMES + CARDS_FRAMES + desktop.replace("cards-desktop", "cards-desktop-2") + "</section>", ['2 desktop frames of direction "cards", needs exactly one']),
            "only-the-directions-frames": (CARDS_FRAMES, ["no desktop frame\n", "no phone frame\n"]),
            "frame-illustrating-nothing": (DEFAULT_FRAMES + desktop + "</section>" + phone.replace(' data-illustrates="acceptance-rule"', "") + "</section>", ['phone frame of direction "cards" names no spec anchor']),
        }
        for case, (frames, problems) in cases.items():
            with self.subTest(case=case):
                base = self.shaped_base()
                shutil.rmtree(self.mockup_set(), ignore_errors=True)
                mockup = self.write_mockup(frames=directions(PICK, CARDS) + frames)
                self.assertFailsNaming(self.validate(base), mockup, *(f"{mockup}: {words}" for words in problems), problems=len(problems))

    # repair-specs #rule-source-validator: --whole-spec, repair's whole-spec check, runs every check the validator makes
    # on what a change adds or edits on every criterion and mockup of the spec.
    def test_whole_spec_runs_markup_checks_on_every_criterion(self):
        """prompt-first-shaping #complete-criterion-judged: a criterion that misses a When or an outcome passes
        unchanged, under an unchanged spec or beside an edited criterion, and fails the whole-spec check either way."""
        cases = {
            "no-when": ({"scenario_text": "Given the system is ready"}, "acceptance-rule scenario needs a When keyword"),
            "short-observable": ({"observable_text": "done"}, "acceptance-rule observable needs a verifiable outcome"),
        }
        for name, (unedited, error) in cases.items():
            with self.subTest(case=name):
                self.write_shaped_spec(extra_criteria=self.OTHER.format("Then the list shows the item."), **unedited)
                shutil.copy2(FALLBACK, self.style)
                base = self.commit()
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "style=fallback stories=unchanged sections=legacy\n")
                result = self.validate(base, whole_spec=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.write_shaped_spec(extra_criteria=self.OTHER.format("Then the list shows the renamed item."), **unedited)
                self.assertEqual(self.validate(base).returncode, 0)
                result = self.validate(base, whole_spec=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_whole_spec_checks_stories_and_sections_of_an_unchanged_spec(self):
        """repair-specs #rule-source-scope: no spec is grandfathered; an unchanged spec with no story passes the
        validator and fails the whole-spec check; a well-formed unchanged spec passes it as a valid one."""
        self.write_spec(body="<p>Legacy</p>")
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        self.assertEqual(self.validate(base).returncode, 0)
        result = self.validate(base, whole_spec=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "governing spec has no explicit data-user-story\n")  # no "changed": it is not
        self.write_shaped_spec()
        base = self.commit()
        result = self.validate(base, whole_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")

    def test_whole_spec_warns_on_every_criterion_whose_then_may_span_two_screens(self):
        """prompt-first-shaping #complete-criterion-judged: an unchanged two-tab criterion raises no warning, and the
        whole-spec check warns on it, naming it, and passes."""
        self.write_shaped_spec(observable_text="Then the Transcript tab marks the hook and the Overview tab names it.")
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.two_screen_warnings(result), [], result.stderr)
        result = self.validate(base, whole_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        warnings = self.two_screen_warnings(result)
        self.assertEqual(len(warnings), 1, result.stderr)
        self.assertIn("acceptance-rule", warnings[0])
        self.assertIn("Transcript tab", warnings[0])

    def test_whole_spec_warns_on_every_clause_stating_look_or_motion_mechanics(self):
        """prompt-first-shaping #complete-mechanics-warning, repair-specs #rule-source-validator: an unchanged hex clause
        and choreography table raise no warning, and the whole-spec check warns on each, naming it, and passes."""
        self.write_shaped_spec(behavior='<p data-anchor="behavior-save">The Save button is #0a84ff.</p>' + self.CHOREOGRAPHY.format("200 ms", "300 ms"))
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mechanics_warnings(result), [], result.stderr)
        result = self.validate(base, whole_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        warnings = self.mechanics_warnings(result)
        self.assertEqual(len(warnings), 2, result.stderr)
        self.assertIn('clause behavior-save states look or motion mechanics ("#0a84ff")', warnings[0])
        self.assertIn('table behavior-reveal states look or motion mechanics ("200 ms", "300 ms")', warnings[1])

    def test_whole_spec_runs_fidelity_and_walkthrough_checks_on_every_mockup(self):
        """repair-specs #acceptance-rule-source-validator, mockups #checks-warn: a drawn mockup taking a color from no
        token and unchanged since it was committed passes as it is, fails the whole-spec check naming it and the color,
        and passes it once the color comes from a token its frames declare; an untouched prototype mockup asking at a
        missing step fails it too, and an untouched note reading none warns under it."""
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        untokened = MOCKUP_STYLE + " [data-mockup-frame] button { background: #e11d48; }"
        drawn = self.write_mockup(style=untokened)
        base = self.commit()
        self.assertEqual(self.validate(base).returncode, 0)
        self.assertFailsNaming(self.validate(base, whole_spec=True), drawn, "color #e11d48", problems=1)
        tokened = MOCKUP_STYLE.replace("--ui-ink: #333333;", "--ui-ink: #333333; --ui-alert: #e11d48;") + " [data-mockup-frame] button { background: var(--ui-alert); }"
        self.write_mockup(style=tokened)
        base = self.commit()
        result = self.validate(base, whole_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(drawn, result.stderr)
        prototype = self.write_prototype(body=PROTOTYPE_BODY.replace('data-step="team-open">Should', 'data-step="export">Should'))
        self.write_mockup("02-settings.mockup.html", style=tokened, note="none")
        base = self.commit()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("mockup", result.stderr)
        result = self.validate(base, whole_spec=True)
        self.assertFailsNaming(result, prototype, 'step "export", which its walkthrough lacks', problems=1)
        self.assertIn("warning: mockup docs/specs/domains/mockups/example/02-settings.mockup.html: fidelity note reads none", result.stderr)

    def test_whole_spec_warns_on_a_drawn_mockup_naming_no_chrome(self):
        """repair-specs #acceptance-mockup-drawn, mockups #checks-warn: an untouched drawn mockup naming no chrome passes
        as it is, and the whole-spec check passes with a warning naming the mockup and that a screen the project owns is
        its running prototype; its design source changed since its note fails the whole-spec check, naming the file."""
        self.write_shaped_spec()
        shutil.copy2(FALLBACK, self.style)
        drawn = self.write_mockup(chrome=None)
        base = self.commit()
        result = self.validate(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(drawn, result.stderr)
        result = self.validate(base, whole_spec=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
        self.assertEqual(result.stderr.count(drawn), 1, result.stderr)
        self.assertIn(f"warning: mockup {drawn}: names no product whose chrome it draws", result.stderr)
        self.assertIn("a screen the project owns is its running prototype", result.stderr)
        (self.repo / "DESIGN.md").write_text("# Design\n--ui-ink: #222222\n")
        base = self.commit()
        result = self.validate(base, whole_spec=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"mockup {drawn}: design source DESIGN.md changed since {self.design_commit}", result.stderr)

    def test_whole_spec_still_fails_a_dropped_proof_clause_against_the_base(self):
        """repair-specs #rule-source-validator, prompt-first-shaping #acceptance-proof-clause-rule-repair: the whole-spec
        check fails a change dropping a proof clause the base carries, naming the base criterion and clause, and passes
        one keeping it."""
        clause = "proven by a unit test: no screen shows it"
        self.write_shaped_spec(observable_text=f"Then the event is recorded once, {clause}.")
        shutil.copy2(FALLBACK, self.style)
        base = self.commit()
        self.write_shaped_spec(observable_text="Then the event is recorded once.")
        result = self.validate(base, whole_spec=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'criterion acceptance-rule at the base carries the proof clause "{clause}"', result.stderr)
        self.write_shaped_spec(observable_text=f"Then the event is recorded twice, {clause}.")
        self.assertEqual(self.validate(base, whole_spec=True).returncode, 0)

    def test_whole_spec_keeps_a_proof_clause_another_spec_of_the_repo_carries(self):
        """repair-specs #rule-source-validator, prompt-first-shaping #acceptance-proof-clause-rule-repair: under the
        whole-spec check a base criterion's proof clause is kept when a criterion of any spec of the repository carries
        it word for word, so a Home move to an existing spec and a split into a new spec pass; a clause another spec
        only quotes outside its criteria is still dropped. Without the flag, the spec is still checked only against its
        own base."""
        clause = "proven by a unit test: no screen shows it"
        other = self.spec.parent / "other.spec.html"
        plain = "Then the list shows the item."
        # name: (other spec at the base or None, this spec after, other spec after, kept)
        cases = {
            "home-move": ("Then the log names the item.", (plain, None),
                          ("Then the log names the item.", f"Then the event is recorded once, {clause}."), True),
            "split": (None, ("Then the event is recorded once.", None), (f"Then the log names the event, {clause}.", None), True),
            "quoted-outside-criteria": (None, ("Then the event is recorded once.", None), None, False),
        }
        for name, (other_before, after, other_after, kept) in cases.items():
            with self.subTest(case=name):
                other.unlink(missing_ok=True)
                self.write_shaped_spec(observable_text=plain, extra_criteria=self.OTHER.format(f"Then the event is recorded once, {clause}."))
                if other_before:
                    self.write_shaped_spec(observable_text=other_before, path=other)
                shutil.copy2(FALLBACK, self.style)
                base = self.commit()
                rule, extra = after
                self.write_shaped_spec(observable_text=rule, extra_criteria=self.OTHER.format(extra) if extra else "")
                if other_after:
                    rule, extra = other_after
                    self.write_shaped_spec(observable_text=rule, extra_criteria=self.OTHER.format(extra) if extra else "", path=other)
                else:
                    self.write_shaped_spec(path=other, story=DEFAULT_STORY + f"<p>Each event is {clause}.</p>")
                self.commit()
                label = "acceptance-other"
                result = self.validate(base)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    result.stderr,
                    f'criterion {label} at the base carries the proof clause "{clause}", which no criterion of the '
                    "changed spec carries; keep it word for word (prompt-first-shaping #complete-criterion-proof-clause)\n",
                )
                result = self.validate(base, whole_spec=True)
                if kept:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "style=fallback stories=valid sections=valid\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f'criterion {label} at the base carries the proof clause "{clause}", which no criterion '
                                  "of any spec of the repository carries", result.stderr)
                    self.assertEqual(result.stderr.count("at the base carries"), 1, result.stderr)

    def test_whole_spec_fails_a_proof_clause_of_a_deleted_spec_no_spec_carries(self):
        """repair-specs #rule-source-validator, prompt-first-shaping #acceptance-proof-clause-rule-repair: under the
        whole-spec check a criterion's proof clause in a spec deleted since the base, as when Home merges it into
        another, passes while a criterion of any spec carries it word for word, and fails naming the deleted spec, its
        criterion, and the clause when none does. Without the flag, a deleted spec's clauses are checked nowhere, as
        before."""
        clause = "proven by a unit test: no screen shows it"
        merged = self.spec.parent / "merged.spec.html"
        plain = "Then the list shows the item."
        for name, carried in (("merged-in", True), ("dropped", False)):
            with self.subTest(case=name):
                self.write_shaped_spec()
                self.write_shaped_spec(observable_text=f"Then the event is recorded once, {clause}.", path=merged)
                shutil.copy2(FALLBACK, self.style)
                base = self.commit()
                merged.unlink()
                self.write_shaped_spec(extra_criteria=self.OTHER.format(f"Then the event is recorded once, {clause}." if carried else plain))
                self.commit()
                result = self.validate(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.validate(base, whole_spec=True)
                if carried:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        result.stderr,
                        "criterion acceptance-rule of deleted spec docs/specs/domains/merged.spec.html at the base carries "
                        f'the proof clause "{clause}", which no criterion of any spec of the repository carries; keep it '
                        "word for word (prompt-first-shaping #complete-criterion-proof-clause)\n",
                    )

if __name__ == "__main__":
    unittest.main()

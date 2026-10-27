"""Report every acceptance group and atomic child as pass, fail, missing or external.

The existing 71-group ledger stays authoritative for group procedures; the atomic children
come from the generated inventory (services/bench/specification).
Only trusted registered checks execute; a child passes only on its own executed evidence of the
exact class it requires, a group only when every child passes, and completion additionally
needs a fully reviewed census and authenticated qualification, which no path here can grant.
Component runs cannot authenticate clean-machine, independent-user or cross-platform claims.
No catalog is mutated.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'services/bench/specification/parity-cases.json'
BASELINE = ROOT / 'docs/testing/scientific-acceptance.md'
# Trusted code selects executable tests; imported evidence/ledger text is never a command.
REGRESSIONS = {
    'R01.producing-history': {
        'tests': ('tests/test_scientific_history.py', 'tests/test_scientific_provenance.py',
                  'tests/test_scientific_provenance_resolve.py', 'tests/test_scientific_provenance_paths.py',
                  'tests/test_scientific_exchange.py'),
        'scope': 'Durable producing histories and conflict copies with synthetic workers.',
        'remaining': 'Actual concurrent kernels and manual edits through UI, restart and export.',
    },
    'R02.new-layer-publication': {
        'tests': ('tests/test_scientific_outputs.py', 'tests/test_scientific_output_sets.py'),
        'scope': 'New-output validation and atomic publication with controlled workers.',
        'remaining': 'Actual Python and plugin creation of all seven families, acceptance and reopening through UI.',
    },
    'R03.multi-layer-drafts': {
        'tests': ('tests/test_scientific_group_save.py', 'tests/test_scientific_saves.py'),
        'scope': 'Multiple draft capture, publication failure, conflict and retry in retained storage.',
        'remaining': 'Ordinary multi-layer editing and independent draft recovery through the working UI.',
    },
    'R04.resource-only-code': {
        'tests': ('tests/test_scientific_resource_edits.py',),
        'scope': 'Exact resource-only publication and stale-base refusal without a rendered frame.',
        'remaining': 'Actual code execution on undisplayed inputs through public controls and shared permissions.',
    },
    'R05.empty-install-intake': {
        'tests': ('tests/test_scientific_file_intake.py', 'tests/test_scientific_file_api.py'),
        'scope': 'Retained upload, validation, duplicate retry and failure preservation through HTTP/storage.',
        'remaining': 'Packaged empty installation, ordinary dataset/reader selection and reset-target preparation.',
    },
    'R06.native-composition': {
        'tests': ('tests/test_scientific_ports.py', 'tests/test_scientific_tools.py'),
        'scope': 'Typed declarations plus retained scene/tool selection, exact revision preservation and explicit initialization with controlled workers.',
        'remaining': 'Native/structured multiple inputs and retained outputs across all execution routes and runtimes.',
    },
    'R07.direct-3d': {
        'tests': ('tests/test_scientific_view_wire.py',),
        'scope': 'View/frame transport validation only; no gesture or graphics qualification.',
        'remaining': 'Actual orbit/pan/zoom and mixed 3D picking at multiple DPI and viewport sizes.',
    },
    'R08.specialist-widget': {
        'tests': ('tests/test_scientific_native_panel_wire.py',),
        'scope': 'Bounded panel transport, stale-event rejection and callback error containment.',
        'remaining': 'Actual stateful specialist panel, continuous gestures, asynchronous work, reconnect and persistence.',
    },
    'R09.truthful-closure': {
        'tests': ('tests/test_scientific_acceptance.py', 'tests/test_scientific_inventory.py'),
        'scope': 'Incomplete coverage, forged qualification flags and missing execution evidence refuse closure.',
        'remaining': 'Reviewed atomic source coverage, authenticated independent evidence and final artifact qualification.',
    },
}


INVENTORY = ROOT / 'services/bench/specification/atomic-cases.json'
TICKET = ROOT / 'docs/testing/scientific-reviewed-rows.md'
STATES = ('pass', 'fail', 'missing', 'external')
CLOSURE = 'tests/test_scientific_acceptance.py::'
TABLES = 'tests/test_scientific_tables.py::'
FAMILIES_TESTS = 'tests/test_scientific_families.py::'
REBASE = 'tests/test_scientific_rebase.py::'
RESOLVE = 'tests/test_scientific_provenance_resolve.py::'
URLS = 'tests/test_scientific_url_intake.py::'
STACKS = 'tests/test_scientific_stacks.py::'
CHOICE = 'tests/test_scientific_reader_choice.py::'
SAMPLES = 'tests/test_scientific_samples.py::'
from bench import scientific_checks_h04  # noqa: E402
from bench import scientific_checks_h07  # noqa: E402
INCREMENTAL = 'tests/test_scientific_incremental.py::'
from bench import scientific_checks_h06b  # noqa: E402
H06A_RUNTIMES = scientific_checks_h06b.REQUIRES
H06A_TESTS = {
    'labels': ('tests/test_scientific_image_labels_browser.py::test_browser_labels_display_reaches_native_napari_state',
               'tests/test_scientific_image_labels_parity.py::test_actual_labels_display_matches_pinned_napari_and_independent_values'),
    'image': ('tests/test_scientific_image_labels_browser.py::test_browser_image_controls_reach_native_napari_state',
              'tests/test_scientific_image_labels_parity.py::test_actual_image_controls_and_histogram_match_pinned_napari_and_independent_values'),
}
H06A_TESTS = {family: (*tests, scientific_checks_h06b.RUNTIME) for family, tests in H06A_TESTS.items()}
H06A_ERASE = 'tests/test_scientific_image_labels_parity.py::test_erase_writes_the_changed_background_label_as_napari'
H06A_MISSING = 'tests/test_scientific_image_labels_parity.py::test_actual_missing_values_keep_nan_color_and_render_as_napari'
H06A_CHILDREN = {
    'labels': ('labelcolormode-auto', 'labelcolormode-direct', 'qtdisplayselectedlabelcheckboxcontrol',
               'api-directlabelcolormap-color-dict', 'api-directlabelcolormap-default-color', 'api-labelcolormapbase-background-value',
               'api-labelcolormapbase-selection', 'api-labelcolormapbase-use-selection', 'api-directlabelcolormap-selection',
               'api-directlabelcolormap-use-selection', 'labels-new-colormap', 'api-cycliclabelcolormap-map', 'api-directlabelcolormap-map',
               'labels-get-color', 'qtcolorbox', 'labels-features',
               'step-1-select-labels-change-direct-and-automatic', 'step-2-switch-supported-display-modes'),
    'image': ('intensityvisualizationmixin-contrast-limits', 'intensityvisualizationmixin-gamma', 'intensityvisualizationmixin-colormap',
              'intensityvisualizationmixin-colormaps', 'intensityvisualizationmixin-auto-contrast',
              'intensityvisualizationmixin-contrast-limits-range', 'intensityvisualizationmixin-reset-contrast-limits',
              'intensityvisualizationmixin-reset-contrast-limits-range', 'image-interpolation2d',
              *(f'interpolation-{mode}' for mode in ('bessel', 'blackman', 'catrom', 'cubic', 'custom', 'gaussian', 'hamming', 'hanning',
                                                     'hermite', 'kaiser', 'lanczos', 'linear', 'mitchell', 'nearest', 'spline16', 'spline36')),
              'scalarfieldbase-custom-interpolation-kernel-2d', 'scalarfieldbase-dtype', 'image-histogram', 'histogram-histogrammodel-bins',
              'histogram-histogrammodel-enabled', 'histogram-histogrammodel-log-scale', 'histogram-histogrammodel-mode',
              'histogram-histogrammodel-max-samples', 'histogram-histogrammodel-counts', 'histogram-histogrammodel-compute',
              'histogram-histogrammodel-reset', 'auto-contrast-once', 'shortcut-napari-auto-contrast-once',
              'api-colormap-colors', 'api-colormap-controls', 'api-colormap-interpolation', 'api-colormap-name', 'api-colormap-nan-color',
              'api-colormap-high-color', 'api-colormap-low-color', 'api-colormapinterpolationmode-linear',
              'api-colormapinterpolationmode-zero', 'api-ensure-colormap', 'step-1-inspect-values-and-histograms',
              'step-2-change-appearance-and-channel-selection'),
}
FAMILY_SAVES = 'tests/test_scientific_family_saves.py::'
from bench.scientific_harnesses import checks as harness_checks  # noqa: E402
OUTPUTS_UPSTREAM = 'tests/test_scientific_outputs_upstream.py::test_created_output_is_accepted_and_reopens_with_exact_values'
R02_CASES = [f'python-{kind}' for kind in ('image', 'labels', 'points', 'shapes', 'surface', 'vectors', 'tracks')] + \
            [f'plugin-{kind}' for kind in ('image', 'labels', 'points', 'shapes', 'surface')]  # no builtin plugin makes vectors or tracks
PATHS = 'tests/test_scientific_provenance_paths.py::'
EXCHANGE = 'tests/test_scientific_exchange.py::'
# Trusted executable checks per atomic child (pytest node-id prefixes under services/). A check
# passes its child only when its evidence class is exactly the class the child requires.
CHECKS = {
    **{f'{case}.component': {'tests': entry['tests'], 'class': 'synthetic_fixture'}
       for case, entry in REGRESSIONS.items()},
    **{f'W.relations-calibration.{suffix}': {'tests': tuple(TABLES + t for t in tests), 'class': 'synthetic_fixture'}
       for suffix, tests in {
           'typed-table-columns': ('test_published_table_reopens_with_exact_types_bytes_and_missing_values',
                                   'test_values_that_disagree_with_their_declaration_are_refused_before_anything_is_written',
                                   'test_a_changed_column_or_schema_never_reads_as_the_accepted_table'),
           'table-relation-integrity': ('test_relations_and_lineage_are_checked_against_the_key_they_reference',
                                        'test_replacing_needs_consent_keeps_earlier_revisions_and_refuses_orphaned_relations'),
           'table-atomic-publication': ('test_stale_revision_conflicts_and_a_lost_acknowledgement_retries_exactly',
                                        'test_empty_tables_keep_their_schema',
                                        'test_pages_are_bounded_and_rows_beyond_one_chunk_stay_in_order'),
           'table-copies-honest-object-space': ('test_copies_keep_tables_and_name_the_measured_labels_honestly',),
       }.items()},
    'W.relations-calibration.measure-typed-object-table': {'tests': (
        TABLES + 'test_measure_publishes_a_typed_object_table_and_still_reads_earlier_json_measurements',
        'tests/test_scientific_resource_actions.py'), 'class': 'synthetic_fixture'},
    'W.relations-calibration.table-api-pages': {'tests': (
        'tests/test_scientific_resource_api.py::test_tables_list_and_page_typed_columns_of_an_exact_revision',),
        'class': 'synthetic_fixture'},
    'P3.tracks.lineage-graph-table': {'tests': (
        TABLES + 'test_relations_and_lineage_are_checked_against_the_key_they_reference',), 'class': 'synthetic_fixture'},
    **{child: {'tests': tuple(FAMILIES_TESTS + t for t in tests), 'class': 'synthetic_fixture'} for child, tests in {
        'W.relations-calibration.embedding-keyed-rows': (
            'test_every_family_is_published_in_one_cas_and_reopens_exactly',
            'test_one_wrong_part_publishes_nothing_of_any_family',
            'test_replacing_the_table_an_embedding_follows_needs_the_embedding_too',
            'test_publishing_another_embedding_cannot_carry_an_old_one_over_replaced_keys',
            'test_an_embedding_whose_keys_changed_under_it_does_not_open'),
        'W.relations-calibration.family-transaction-atomic': (
            'test_one_wrong_part_publishes_nothing_of_any_family',
            'test_a_lost_acknowledgement_retries_exactly_and_a_stale_revision_conflicts'),
        'W.projects.trained-artifact-retained': (
            'test_every_family_is_published_in_one_cas_and_reopens_exactly',
            'test_a_changed_artifact_is_listed_as_unreadable_never_dropped_or_executed'),
        'W.projects.families-survive-copies': ('test_copies_keep_every_family_and_their_references',),
        'W.animation.animation-document-retained': (
            'test_every_family_is_published_in_one_cas_and_reopens_exactly',
            'test_one_wrong_part_publishes_nothing_of_any_family[bad-json-not valid JSON]'),
        'P3.lifecycle.resource-inventory': (
            'test_the_registry_names_every_family_of_the_work_package_and_its_opener',
            'test_resources_and_artifact_download_through_the_api'),
    }.items()},
    **{child: {'tests': tuple(REBASE + t for t in tests), 'class': 'synthetic_fixture'} for child, tests in {
        'R03.multi-layer-drafts.session-saves-do-not-strand': ('test_saving_one_layer_never_strands_another_or_its_own_next_save',),
        'R03.multi-layer-drafts.saved-onto-newer-revision': ('test_another_sessions_save_of_a_different_layer_is_kept_and_this_save_lands_on_it',),
        'R03.multi-layer-drafts.granular-conflict-report': ('test_a_changed_layer_conflicts_layer_by_layer_and_the_rest_can_still_be_saved',),
        'R03.multi-layer-drafts.lost-acknowledgement-tracked': ('test_a_lost_acknowledgement_still_records_what_the_session_now_comes_from',),
        'P4.edit-conflicts.changed-layer-never-overwritten': ('test_a_changed_layer_conflicts_layer_by_layer_and_the_rest_can_still_be_saved',),
    }.items()},
    'R01.producing-history.resolves-own-producer': {'tests': (
        RESOLVE + 'test_two_kernels_and_a_manual_edit_each_resolve_only_their_own_producer_after_restart',
        RESOLVE + 'test_a_copy_of_unchanged_values_keeps_their_producer_remapped_to_the_copy'),
        'class': 'synthetic_fixture'},
    'R01.producing-history.chunk-change-record': {'tests': (
        RESOLVE + 'test_change_record_names_exactly_the_chunks_whose_bytes_differ',), 'class': 'synthetic_fixture'},
    'R01.producing-history.no-substituted-history': {'tests': (
        RESOLVE + 'test_an_unreadable_history_is_reported_and_never_replaced_by_another_sharing_the_input',
        RESOLVE + 'test_a_record_written_outside_publication_is_reported_against_its_base_kernel_and_input',
        RESOLVE + 'test_kept_provenance_says_when_it_was_accepted_and_reports_values_changed_later',
        RESOLVE + 'test_a_copy_saved_before_records_were_remapped_says_so_not_that_values_changed',
        RESOLVE + 'test_a_damaged_earlier_revision_is_a_problem_not_a_failed_lookup'),
        'class': 'synthetic_fixture'},
    'R01.producing-history.unrecorded-said': {'tests': (
        RESOLVE + 'test_values_accepted_without_a_producer_say_so_and_a_later_unrecorded_publication_drops_the_old_record',),
        'class': 'synthetic_fixture'},
    'R01.producing-history.complete-record': {'tests': (
        RESOLVE + 'test_the_accepted_record_keeps_runtimes_coordinates_its_change_and_what_was_not_recorded',
        RESOLVE + 'test_a_saved_change_record_that_disagrees_with_the_bytes_is_reported'), 'class': 'synthetic_fixture'},
    'R01.producing-history.every-publication-path': {'tests': (
        PATHS + 'test_a_multi_layer_save_resolves_every_layer_to_one_producer_after_a_lost_acknowledgement',
        PATHS + 'test_two_multi_layer_saves_from_one_base_conflict_explicitly_and_the_copy_keeps_its_producer',
        PATHS + 'test_a_new_output_resolves_its_producer_and_base_after_a_failed_publication_is_retried',
        PATHS + 'test_a_producer_record_hooke_cannot_check_is_kept_as_given_not_as_verified'), 'class': 'synthetic_fixture'},
    'R01.producing-history.undo-and-unannounced-changes': {'tests': (
        PATHS + 'test_an_edit_undone_before_saving_records_its_actions_and_no_changed_values',
        PATHS + 'test_a_change_no_action_announced_is_in_the_change_record_and_said_to_be_unattributed',
        PATHS + 'test_an_edit_undone_and_redone_before_saving_records_all_three_and_its_one_changed_block'),
        'class': 'synthetic_fixture'},
    'R01.producing-history.bundle-reimport-resolves': {'tests': (
        EXCHANGE + 'test_every_result_reimported_into_another_profile_resolves_exactly_as_it_did',
        EXCHANGE + 'test_a_bundle_carries_exact_bytes_under_portable_names',
        EXCHANGE + 'test_export_and_import_through_http_between_two_profiles'), 'class': 'synthetic_fixture'},
    'R01.producing-history.bundle-reimport-safe': {'tests': (
        EXCHANGE + 'test_reimport_is_idempotent_and_extends_a_shorter_history_it_already_holds',
        EXCHANGE + 'test_a_different_document_with_the_same_identity_is_never_overwritten',
        EXCHANGE + 'test_a_damaged_or_foreign_bundle_imports_nothing',
        EXCHANGE + 'test_work_beyond_the_bundle_allowance_is_refused_and_says_so',
        EXCHANGE + 'test_an_incomplete_or_invalid_bundle_imports_nothing_and_leaves_no_files',
        EXCHANGE + 'test_a_damaged_copy_this_profile_holds_is_repaired_by_a_good_bundle',
        EXCHANGE + 'test_a_save_racing_an_import_cannot_overwrite_the_imported_revision',
        EXCHANGE + 'test_a_failure_while_placing_takes_back_everything_this_import_placed',
        EXCHANGE + 'test_a_save_landing_between_two_placements_is_never_lost',
        EXCHANGE + 'test_a_present_but_damaged_object_is_replaced_by_a_good_bundle',
        EXCHANGE + 'test_a_damaged_archive_is_refused_and_imports_nothing',
        EXCHANGE + 'test_a_corrupt_archive_posted_over_http_is_a_refusal_not_a_server_error'), 'class': 'synthetic_fixture'},
    'R01.producing-history.no-fabricated-provenance': {'tests': (
        RESOLVE + 'test_document_saves_cannot_add_change_or_remove_provenance',
        RESOLVE + 'test_provenance_endpoint_and_the_studio_save_refusing_forged_provenance'), 'class': 'synthetic_fixture'},
    # H03 (ticket 49): ordinary intake, declared reader choice, stacks and samples.
    **{child: {'tests': tests, 'class': 'synthetic_fixture'} for child, tests in {
        'R05.empty-install-intake.url-untrusted-refused': (
            URLS + 'test_addresses_hooke_will_not_download_from_are_refused_before_any_request',
            URLS + 'test_untrusted_answers_are_refused_and_keep_nothing',
            URLS + 'test_requests_carry_no_credentials_cookies_or_environment_proxy',
            URLS + 'test_the_client_ignores_environment_settings_and_follows_no_redirect_by_itself',
            URLS + 'test_download_needs_the_studio_and_the_current_project',
            URLS + 'test_a_nat64_address_of_a_public_host_is_allowed'),
        'R05.empty-install-intake.url-exact-retained': (
            URLS + 'test_download_keeps_the_exact_bytes_with_a_receipt_and_a_retry_does_not_download_again',
            URLS + 'test_a_followed_redirect_is_recorded_with_where_the_bytes_came_from',
            URLS + 'test_a_request_identity_cannot_be_reused_for_another_address',
            URLS + 'test_studio_saves_cannot_forge_or_change_where_a_source_came_from',
            URLS + 'test_the_store_refuses_a_receipt_that_does_not_match_the_retained_bytes',
            URLS + 'test_an_identical_request_finishing_first_elsewhere_answers_with_its_receipt'),
        'R05.empty-install-intake.url-bounded-cancellable': (
            URLS + 'test_stopping_during_the_transfer_keeps_nothing_and_a_later_retry_succeeds',
            URLS + 'test_the_deadline_bounds_the_whole_download',
            URLS + 'test_a_disconnected_studio_stops_the_download_and_releases_its_memory',
            URLS + 'test_untrusted_answers_are_refused_and_keep_nothing[too-large-limited to 64 MiB. Nothing was downloaded]',
            URLS + 'test_untrusted_answers_are_refused_and_keep_nothing[grows-grew past]',
            URLS + 'test_a_stuck_name_lookup_is_abandoned_and_refused',
            URLS + 'test_a_stage_that_cannot_be_removed_is_retried_later'),
        'R05.empty-install-intake.stack-ordered-axis': (
            STACKS + 'test_a_stack_keeps_the_researchers_order_its_axis_and_each_exact_file',
            STACKS + 'test_a_stack_that_one_reader_could_not_open_plane_by_plane_is_refused_and_keeps_nothing',
            STACKS + 'test_how_files_were_stacked_cannot_be_rewritten_by_a_save',
            STACKS + 'test_hidden_or_trailing_dot_names_have_no_file_type'),
        'P7.readers.declared-capability-choice': (
            CHOICE + 'test_one_exact_reader_is_chosen_and_inexact_or_undeclared_readers_are_refused_precisely',
            CHOICE + 'test_several_exact_readers_are_the_researchers_choice',
            CHOICE + 'test_no_reader_and_no_declared_match_say_what_is_installed',
            CHOICE + 'test_matching_readers_that_cannot_open_exactly_now_each_say_why',
            CHOICE + 'test_folder_readers_check_their_required_metadata_and_entry_names',
            CHOICE + 'test_missing_setup_and_device_limits_are_said_apart_from_incompatibility',
            CHOICE + 'test_a_declaration_that_states_no_kept_facts_is_the_integrations_to_update'),
        'P7.readers.inexact-reader-refused': (
            CHOICE + 'test_one_exact_reader_is_chosen_and_inexact_or_undeclared_readers_are_refused_precisely',
            CHOICE + 'test_refused_reader_choice_over_http_keeps_the_source_and_says_the_next_step'),
        'P7.readers.format-three-declarations': (
            CHOICE + 'test_format_three_declarations_are_bounded_and_explicit',
            SAMPLES + 'test_sample_declarations_are_pinned_https_files'),
        'P7.readers.stack-readers-ordered': (
            STACKS + 'test_stack_readers_are_chosen_by_declaration_and_receive_the_planes_in_order',
            STACKS + 'test_stack_reader_patterns_match_the_files_not_the_collection_label'),
        'P7.samples-commands.declared-sample-pinned': (
            SAMPLES + 'test_samples_are_listed_from_declarations_with_their_readers_and_nothing_runs',
            SAMPLES + 'test_a_sample_opens_at_its_pinned_digest_and_its_reader_is_then_chosen',
            SAMPLES + 'test_bytes_that_differ_from_the_declaration_are_refused_and_keep_nothing',
            SAMPLES + 'test_an_undeclared_sample_is_refused_before_any_download',
            SAMPLES + 'test_a_sample_open_needs_the_studio',
            SAMPLES + 'test_a_sample_must_be_exactly_its_declared_size',
            SAMPLES + 'test_samples_of_a_catalog_whose_code_is_not_allowed_are_listed_but_not_opened'),
    }.items()},
    **{child: {'tests': tuple(INCREMENTAL + t for t in tests), 'class': 'synthetic_fixture'} for child, tests in {
        'P5.incremental-workflow.changed-chunks-only': (
            'test_one_changed_chunk_of_an_80_mib_layer_is_the_only_one_sent_written_and_hashed',),
        'P5.incremental-workflow.false-unchanged-claim-refused': (
            'test_a_false_unchanged_claim_is_refused_and_nothing_is_accepted',
            'test_a_kept_chunk_out_of_order_or_with_bytes_is_refused',
            'test_new_outputs_have_no_base_so_every_chunk_is_sent'),
        'P5.incremental-workflow.inherited-chunks-checked': (
            'test_inherited_chunks_must_be_present_and_their_bytes_are_checked_on_every_read',),
        'P5.incremental-workflow.explicit-budgets': (
            'test_budgets_bound_new_bytes_and_logical_size_not_a_whole_array_cap',
            'test_too_many_changed_bytes_for_memory_are_refused_before_any_transport',
            'test_the_memory_budget_is_shared_by_every_layer_of_one_save'),
        'P5.incremental-workflow.multi-layer-incremental': ('test_several_layers_each_send_only_their_changed_chunks',),
        'P5.incremental-workflow.base-digests-bound': ('test_base_digests_travel_bound_to_their_request',),
    }.items()},
    'P5.incremental-workflow.native-large-layer-save': {'tests': (
        'tests/test_lazy_large_layers.py::test_a_labels_layer_over_64_mib_is_edited_and_saved_sending_only_its_changed_chunk',),
        'class': 'actual_upstream_worker', 'requires': ('NAPARI_NATIVE_HOST_PREPARATION',)},
    'P5.incremental-workflow.measure-export-bounded-work': {'tests': (
        'tests/test_scientific_resource_actions.py::test_measurement_reads_each_stored_chunk_once_and_matches_every_index',
        'tests/test_scientific_resource_actions.py::test_export_reads_each_chunk_a_bounded_number_of_times_or_refuses'),
        'class': 'synthetic_fixture'},
    'R09.truthful-closure.missing-child': {'tests': (CLOSURE + 'test_missing_child_keeps_group_open',), 'class': 'synthetic_fixture'},
    'R09.truthful-closure.missing-ui-path': {'tests': (CLOSURE + 'test_lower_class_check_cannot_pass_manual_child',), 'class': 'synthetic_fixture'},
    'R09.truthful-closure.stale-artifact': {'tests': (CLOSURE + 'test_stale_evidence_is_not_current',
                                                      CLOSURE + 'test_retained_reports_cannot_be_edited_or_imported'),
                                            'class': 'synthetic_fixture'},
    'R09.truthful-closure.null-criterion': {'tests': (CLOSURE + 'test_null_criterion_cannot_pass',), 'class': 'synthetic_fixture'},
    'R09.truthful-closure.required-skip': {'tests': (CLOSURE + 'test_skipped_test_fails_child',), 'class': 'synthetic_fixture'},
    'R09.truthful-closure.unauthenticated-admission': {'tests': (
        CLOSURE + 'test_flags_cannot_self_certify_even_with_plausible_evidence',
        CLOSURE + 'test_external_evidence_cannot_be_declared_in_the_inventory'), 'class': 'synthetic_fixture'},
    # H06a: each child passes only when its production-Studio journey in a real browser on an isolated profile AND the
    # actual-worker comparison with plain pinned napari (observations, renders, independent values) both pass.
    **{f'P3.{family}.{child}': {'tests': H06A_TESTS[family], 'class': 'isolated_profile_browser', 'requires': H06A_RUNTIMES}
       for family, children in H06A_CHILDREN.items() for child in children},
    # nan_color matters only where values are missing: the floating NaN fixture's comparison too (its render, and
    # Colormap.map and the thumbnail, which use nan_color).
    # The background label is scientific in napari (Erase writes it): its erase parity runs with the journeys.
    'P3.labels.api-labelcolormapbase-background-value': {'tests': (*H06A_TESTS['labels'], H06A_ERASE),
                                                         'class': 'isolated_profile_browser', 'requires': H06A_RUNTIMES},
    'P3.image.api-colormap-nan-color': {'tests': (*H06A_TESTS['image'], H06A_MISSING), 'class': 'isolated_profile_browser',
                                        'requires': H06A_RUNTIMES},
    'R09.truthful-closure.absent-runtime': {'tests': (CLOSURE + 'test_a_check_whose_runtime_is_absent_stays_missing',),
                                            'class': 'synthetic_fixture'},
    # R02 on the actual confined worker: each created output's own parametrized case, and all of them for no startup binding.
    **{f'R02.new-layer-publication.{case}': {'tests': (f'{OUTPUTS_UPSTREAM}[{case}]',), 'class': 'actual_upstream_worker',
                                             'requires': ('NAPARI_NATIVE_HOST_PREPARATION',)} for case in R02_CASES},
    'R02.new-layer-publication.no-startup-binding': {'tests': (OUTPUTS_UPSTREAM,), 'class': 'actual_upstream_worker',
                                                     'requires': ('NAPARI_NATIVE_HOST_PREPARATION',)},
    **{f'R04.resource-only-code.{child}': {'tests': (f'tests/test_scientific_outputs_upstream.py::{test}',),
                                           'class': 'actual_upstream_worker', 'requires': ('NAPARI_NATIVE_HOST_PREPARATION',)}
       for child, test in (('compute-undisplayed', 'test_resource_only_code_publishes_from_undisplayed_input'),
                           ('no-fake-receipt', 'test_resource_only_publication_invents_no_frame_receipt'))},
    **{f'W.relations-calibration.{child}': {'tests': tuple(FAMILY_SAVES + t for t in tests), 'class': 'synthetic_fixture'}
       for child, tests in {
        'drafts-publish-together': ('test_python_and_plugin_drafts_publish_together_in_one_revision',
                                    'test_a_review_lists_and_saves_drafts_through_its_ordinary_session'),
        'draft-object-space-unsaved-refused': ('test_a_layer_with_unsaved_changes_cannot_name_its_object_space',
                                               'test_the_python_side_cannot_name_an_input_it_was_not_granted_or_change_a_draft',
                                               'test_a_draft_declared_from_unsaved_values_is_refused_even_after_they_revert'),
        'draft-transfer-checked': ('test_the_parent_accepts_only_the_declared_parts_in_order',
                                   'test_a_forged_draft_listing_is_refused',
                                   'test_every_draft_hash_is_verified_before_anything_is_sent'),
        'draft-invalid-saves-nothing': ('test_an_invalid_draft_saves_nothing',
                                        'test_a_newer_revision_during_save_saves_nothing_and_a_new_save_joins_it',
                                        'test_a_refused_publication_is_not_started',
                                        'test_saving_the_same_drafts_twice_is_refused_and_leaves_the_session_usable'),
    }.items()},
    **{f'W.relations-calibration.{child}': {'tests': (f'tests/test_scientific_outputs_upstream.py::{test}',),
                                           'class': 'actual_upstream_worker', 'requires': ('NAPARI_NATIVE_HOST_PREPARATION',)}
       for child, test in (('drafts-actual-worker', 'test_python_and_layer_feature_drafts_publish_together_on_the_actual_worker'),
                           ('draft-unsaved-actual-worker', 'test_a_draft_naming_a_layer_with_unsaved_changes_is_refused_on_the_actual_worker'))},
    # H00 templated harnesses: one parametrized case of the template's shared test per child (bench/scientific_harnesses.py).
    **harness_checks(),
    # H04 (P1 dimensions): isolated-profile browser checks against the actual worker and pinned napari.
    **scientific_checks_h04.checks(),
    **scientific_checks_h07.checks(),
    # H06b (Points and Shapes): browser journey, actual-worker comparison, refusals and the harness self-check.
    **scientific_checks_h06b.checks(),
}


class InvalidAcceptance(ValueError):
    pass


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def required_ids():
    import re
    return set(re.findall(r'\| `((?:P[1-8]|Q|W)\.[^`]+)` \|', BASELINE.read_text()))


def regression_inventory():
    """List executable components alongside the obligations they cannot establish."""
    import re
    required = set(re.findall(r'\| (R\d\d\.[a-z0-9-]+) \|', BASELINE.read_text()))
    if set(REGRESSIONS) != required:
        raise InvalidAcceptance('Registered regressions differ from the handoff')
    for case, entry in REGRESSIONS.items():
        for field in ('scope', 'remaining'):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise InvalidAcceptance(f'{case}: missing {field}')
        tests = entry.get('tests')
        if not isinstance(tests, tuple) or not tests:
            raise InvalidAcceptance(f'{case}: missing executable components')
        for name in tests:
            if not isinstance(name, str):
                raise InvalidAcceptance(f'{case}: invalid component test path')
            path = Path(name)
            if (path.is_absolute() or '..' in path.parts or not name.startswith('tests/test_')
                    or path.suffix != '.py' or not (ROOT / 'services' / path).is_file()):
                raise InvalidAcceptance(f'{case}: invalid component test path')
    return {'qualification_eligible': False, 'completion': False, 'regressions': REGRESSIONS}


def validate(ledger):
    """Validate specifications, refusing closure until independent evidence is supported."""
    if type(ledger) is not dict or ledger.get('schema_version') != 1:
        raise InvalidAcceptance('Unsupported acceptance ledger schema')
    cases = ledger.get('cases')
    if type(cases) is not list or any(type(c) is not dict for c in cases):
        raise InvalidAcceptance('Acceptance cases must be records')
    ids = [c.get('id') for c in cases]
    if any(type(i) is not str for i in ids) or len(set(ids)) != len(ids):
        raise InvalidAcceptance('Case IDs must be unique strings')
    required = required_ids()
    if len(required) != 71 or set(ids) != required:
        raise InvalidAcceptance(f'Case coverage differs from the handoff: missing {sorted(required-set(ids))}, extra {sorted(set(ids)-required)}')
    counts = Counter(c.get('family') for c in cases)
    if ledger.get('total_cases') != len(cases) or ledger.get('case_counts') != dict(counts):
        raise InvalidAcceptance('Acceptance counts disagree with the actual cases')
    sources = ledger.get('reference', {}).get('source_evidence', {})
    for case in cases:
        label = case['id']
        if case.get('required') is not True or case.get('family') != label.split('.')[0]:
            raise InvalidAcceptance(f'{label}: required coverage or family changed')
        for name in ('title', 'required_hooke_interface', 'fixture', 'data_invariant', 'oracle'):
            if type(case.get(name)) is not str or not case[name].strip():
                raise InvalidAcceptance(f'{label}: missing {name}')
        if type(case.get('procedure')) is not list or not case['procedure'] or any(type(step) is not str or not step.strip() for step in case['procedure']):
            raise InvalidAcceptance(f'{label}: missing procedure')
        for source in case.get('reference', {}).get('sources', []):
            if source not in sources or len(sources[source].get('sha256', '')) != 64:
                raise InvalidAcceptance(f'{label}: missing source identity')
        # A claimed pass must never become valid merely by editing a JSON flag.
        # Positive qualification import requires H14's independent trust authority;
        # until then this tool deliberately has no path which can grant it.
        statuses = [case.get('status')]
        for section in ('qualification', 'platforms'):
            records = case.get(section)
            expected = {'configured','operational','usable'} if section == 'qualification' else {'macos','windows','linux'}
            if type(records) is not dict or set(records) != expected:
                raise InvalidAcceptance(f'{label}: missing {section} gates')
            statuses.extend(item.get('status') for item in records.values() if type(item) is dict)
            if any(type(item) is not dict for item in records.values()):
                raise InvalidAcceptance(f'{label}: malformed {section} gate')
        if any(status not in {'unqualified','not_run','failed','inconclusive'} for status in statuses):
            raise InvalidAcceptance(f'{label}: independent authenticated qualification is required; flags cannot establish completion')
    return {'groups': len(cases), 'families': dict(counts), 'qualified': 0,
            'completion': False, 'reason': 'Atomic coverage and independent qualification remain open'}


def junit_outcome(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise InvalidAcceptance('The execution has no readable JUnit evidence') from error
    tests = list(root.iter('testcase'))
    if not tests:
        raise InvalidAcceptance('Zero executed tests cannot pass acceptance')
    failures = sum(any(child.tag in {'failure','error'} for child in test) for test in tests)
    skipped = sum(any(child.tag == 'skipped' for child in test) for test in tests)
    return {'tests':len(tests), 'failures':failures, 'skipped':skipped,
            'passed': not failures and not skipped}


def load_inventory(path=INVENTORY, *, ledger=None, checks=None):
    """The atomic children of every group, refused when any group, child or crosswalk is missing."""
    import re
    from bench import scientific_inventory as census
    data = json.loads(Path(path).read_text()) if not isinstance(path, dict) else path
    checks = CHECKS if checks is None else checks
    if type(data) is not dict or data.get('format') != 1 or data.get('archive_sha256') != census.ARCHIVE_SHA256 \
            or data.get('revision') != census.REVISION:
        raise InvalidAcceptance('The atomic inventory is not generated from the pinned napari source')
    for name in ('rules', 'manual'):
        committed = json.loads((census.INVENTORY / f'{name}.json').read_text())
        if data.get(f'{name}_sha256') != census.sha(json.dumps(committed, sort_keys=True).encode()):
            raise InvalidAcceptance(f'atomic-cases.json was not generated from the committed {name}.json: '
                                    'run make scientific-acceptance REGENERATE=1')
    groups = census._groups()
    if set(data.get('groups', {})) != set(groups):
        raise InvalidAcceptance('The atomic inventory groups differ from the handoff groups and regressions')
    children = data.get('children')
    if type(children) is not list or any(type(c) is not dict for c in children):
        raise InvalidAcceptance('Atomic children must be records')
    ids = [c.get('id') for c in children]
    if any(type(i) is not str for i in ids) or len(set(ids)) != len(ids):
        raise InvalidAcceptance('Atomic child ids must be unique strings')
    sources = data.get('sources', {})
    counts = Counter()
    for child in children:
        ident, group = child['id'], child.get('group')
        if group not in groups or not ident.startswith(group + '.') or child.get('owner') != groups[group]['owner']:
            raise InvalidAcceptance(f'{ident}: unknown group, wrong id prefix or owner')
        if child.get('kind') not in census.KINDS or child.get('evidence_class') not in census.EVIDENCE_CLASSES:
            raise InvalidAcceptance(f'{ident}: unknown kind or evidence class')
        if type(child.get('requirement')) is not str or not child['requirement'].strip():
            raise InvalidAcceptance(f'{ident}: missing requirement')
        if 'evidence' in child or 'status' in child:
            raise InvalidAcceptance(f'{ident}: status and evidence come from executed checks, never the inventory')
        source = child.get('source') or {}
        for cited in source.get('all', [source] if source else []):
            if cited.get('path') not in sources:
                raise InvalidAcceptance(f'{ident}: cites a source without its pinned hash')
        counts[group] += 1
    for group in groups:
        if not counts[group] or data['groups'][group].get('children') != counts[group]:
            raise InvalidAcceptance(f'{group}: no atomic children, or a stale child count')
    ledger_ids = {c['id'] for c in (ledger or json.loads(LEDGER.read_text()))['cases']}
    if not ledger_ids <= set(groups):
        raise InvalidAcceptance('A ledger case has no atomic group')
    reviewed = set(re.findall(r'^\| (P4\.[a-z0-9.-]+) \|', TICKET.read_text(), re.M))
    if not reviewed <= set(ids):
        raise InvalidAcceptance(f'Ticket 46 source-review children are missing: {sorted(reviewed - set(ids))[:5]}')
    orphans = sorted(set(checks) - set(ids))
    if orphans:
        raise InvalidAcceptance(f'Registered checks without an atomic child: {orphans}')
    return data


def _testcase_node(case):
    """pytest's JUnit classname is a dotted module path, possibly followed by a class name."""
    parts = case.get('classname', '').split('.')
    for n in range(len(parts), 0, -1):
        candidate = '/'.join(parts[:n]) + '.py'
        if (ROOT / 'services' / candidate).is_file():
            return '::'.join([candidate, *parts[n:], case.get('name', '')])
    return case.get('classname', '') + '::' + case.get('name', '')


def junit_cases(path):
    root = ET.parse(path).getroot()
    out = []
    for case in root.iter('testcase'):
        tags = {child.tag for child in case}
        outcome = 'failed' if tags & {'failure', 'error'} else 'skipped' if 'skipped' in tags else 'passed'
        attributes = dict(case.attrib)
        # --dist loadgroup reports a grouped test as name@group; the node is the test itself.
        attributes['name'] = re.sub(r'@[A-Za-z0-9_.-]+$', '', attributes.get('name', ''))
        out.append({'node': _testcase_node(attributes), 'outcome': outcome})
    return out


def _matches(node, test):
    if '::' not in test:
        return node.split('::', 1)[0] == test
    if '[' in test:  # one parametrized case, exactly
        return node == test
    base = node.split('[', 1)[0]
    return base == test or base.startswith(test + '::')


def child_status(child, results, checks=None):
    """pass, fail, missing or external, with the reason and the evidence it rests on."""
    from bench.scientific_inventory import EXTERNAL_CLASSES
    checks = CHECKS if checks is None else checks
    needed = child['evidence_class']
    unready = [f for f in ('procedure', 'oracle', 'criteria') if not child.get(f)]
    if unready:  # before external: an unspecified child is not ready, whoever would supply its evidence
        return {'status': 'missing', 'reason': f'null {", ".join(unready)}: acceptance is not ready'}
    if needed in EXTERNAL_CLASSES:
        return {'status': 'external', 'reason': f'needs {needed} evidence; no authenticated import exists yet'}
    check = checks.get(child['id'])
    if check is None:
        return {'status': 'missing', 'reason': 'no registered executable check'}
    if check['class'] != needed:
        return {'status': 'missing', 'reason': f'the registered check gives {check["class"]} evidence; this child needs {needed}'}
    if results is None or results.get('stale'):
        return {'status': 'missing', 'reason': (results or {}).get('stale') or 'not run in this invocation'}
    if results.get('kind') == 'aggregate-v1':
        candidates = []
        for leaf in results['leaves']:
            if child['id'] in leaf['run']['selected']:
                status = child_status(child, leaf['run'], checks)
                status['evidence'] = [{**e, 'receipt': leaf['receipt']} for e in status.get('evidence', [])]
                candidates.append(status)
        if not candidates:
            return {'status': 'missing', 'reason': 'not selected in any contributing run'}
        # A complete child belongs to one leaf: never splice tests or runtime guards.
        if any(c['status'] != 'pass' for c in candidates):
            return next(c for c in candidates if c['status'] != 'pass')
        evidence = [e for c in candidates for e in c['evidence']]
        return {'status': 'pass', 'reason': f'complete mandatory checks passed in {len(candidates)} sealed run(s) ({needed})',
                'evidence': evidence}
    if 'selected' in results and child['id'] not in results['selected']:
        return {'status': 'missing', 'reason': 'not selected in this run'}
    if problem := execution_problem(results):
        return {'status': 'fail', 'reason': problem}
    absent = [name for name in check.get('requires', ()) if not results.get('runtime', {}).get(name)]
    if absent:  # its case skips without that runtime; a skip is never a pass, and no runtime is no failure of Hooke
        return {'status': 'missing', 'reason': f'needs the prepared runtime {", ".join(absent)}; not run against it here'}
    cases = [c for c in results['cases'] if any(_matches(c['node'], t) for t in check['tests'])]
    evidence = [{'junit': results['junit'], 'test': c['node'], 'outcome': c['outcome']} for c in cases]
    if not cases:
        return {'status': 'fail', 'reason': 'the registered check executed no test', 'evidence': evidence}
    absent_tests = [test for test in check['tests'] if not any(_matches(c['node'], test) for c in cases)]
    if absent_tests:
        return {'status': 'fail', 'reason': f'mandatory checks did not execute: {", ".join(absent_tests)}',
                'evidence': evidence}
    bad = [c for c in cases if c['outcome'] != 'passed']
    if bad:
        return {'status': 'fail', 'reason': f'{len(bad)} of {len(cases)} mapped tests failed or were skipped',
                'evidence': evidence}
    return {'status': 'pass', 'reason': f'{len(cases)} mapped tests passed ({needed})', 'evidence': evidence}


def group_status(statuses):
    """A group follows its children: any fail, then any missing, then any external; pass only if all pass."""
    if not statuses:
        return 'missing'
    for state in ('fail', 'missing', 'external'):
        if state in statuses:
            return state
    return 'pass'


def select_checks(*, children=(), harnesses=()):
    """Select trusted registrations only; selection never changes the inventory denominator."""
    from bench.scientific_harnesses import HARNESS_TESTS, harness_children
    groups = {'h04': set(scientific_checks_h04.checks()), 'h07': set(scientific_checks_h07.checks())}
    members = harness_children()
    groups.update({name: {c['id'] for c in members if c['harness'] == name} for name in HARNESS_TESTS})
    groups['remaining'] = set(CHECKS) - set().union(*groups.values())
    if not children and not harnesses:
        return dict(CHECKS)
    unknown = set(children) - set(CHECKS)
    unknown_groups = set(harnesses) - set(groups)
    if unknown or unknown_groups:
        raise InvalidAcceptance(f'Unknown registered selection: {sorted(unknown | unknown_groups)}')
    selected = set(children).union(*(groups[name] for name in harnesses))
    if not selected:
        raise InvalidAcceptance('Select at least one registered child')
    return {name: CHECKS[name] for name in sorted(selected)}


def _selectors(checks):
    names = sorted({t for check in checks.values() for t in check['tests']})
    # A whole-file/class/function registration already includes its descendants. Avoid
    # collecting a case twice when another child names one of those descendants.
    return [name for name in names if not any(other != name and _matches(name, other) for other in names)]


def runtime_inputs(runtime):
    """Bind the supplied preparation/configuration and executable files, not merely their names.

    Package/content identity remains the harness's actual runtime guard. Missing paths are
    recorded so a diagnostic run can explain missing preparation without inventing proof.
    """
    return {name: {'path': str(Path(value).resolve()),
                   'sha256': digest(Path(value)) if Path(value).is_file() else None}
            for name, value in sorted(runtime.items())}


def execution_problem(run):
    if run.get('kind') != 'execution-v2':
        # Direct component status helpers predate sealed executions. Reuse never admits them.
        return ''
    if run.get('timed_out') is not False or run.get('exit') != 0:
        return 'execution failed or exceeded its bounded deadline; partial JUnit is not acceptance'
    if run.get('tree_after') != run.get('tree') or run.get('inputs_after') != run.get('inputs'):
        return 'source changed during execution'
    if run.get('runtime_inputs_after') != run.get('runtime_inputs'):
        return 'runtime configuration changed during execution'
    return ''


def run_checks(destination, *, checks=None, timeout=1800, jobs=4, quiet_timing=False):
    """Run the selected registrations with all prerequisites, within one bounded isolated process."""
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise InvalidAcceptance('Execution needs a bounded deadline of 1–1800 seconds')
    if quiet_timing and jobs != 1:
        raise InvalidAcceptance('Quiet timing requires jobs=1; coordinate exclusive quiet access before running')
    if type(jobs) is not int or not 1 <= jobs <= 32:
        raise InvalidAcceptance('Execution needs 1–32 workers')
    checks = CHECKS if checks is None else checks
    if not checks:
        raise InvalidAcceptance('Select at least one registered child')
    selectors = _selectors(checks)
    files = sorted({t.split('::', 1)[0] for t in selectors})
    for name in files:
        path = Path(name)
        if (path.is_absolute() or '..' in path.parts or not name.startswith('tests/test_')
                or not (ROOT / 'services' / path).is_file()):
            raise InvalidAcceptance(f'Invalid registered test path {name}')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise InvalidAcceptance('Execution destination must be empty; preserve earlier evidence')
    profile = destination / 'profile'
    profile.mkdir(mode=0o700, exist_ok=True)
    junit = destination / 'junit.xml'
    # loadgroup keeps a module's xdist_group (one private Studio and browser) on one worker.
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               *(['-p', 'no:xdist', '--timing'] if quiet_timing else []),
               *(['-n', str(jobs), '--dist', 'loadgroup'] if jobs > 1 else []), *selectors,
               f'--basetemp={destination / "temporary"}', f'--junitxml={junit}']
    env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
    env.update(HOME=str(profile), MICROMIND_HOME=str(profile), MICROMIND_KEYCHAIN='off',
               MICROMIND_KEYS_FILE=str(profile / 'absent-keys'), MICROMIND_CATALOG_AUTO='0',
               HF_HUB_OFFLINE='1', PYTHONUTF8='1')
    # A prepared runtime a check requires passes through by its path, and the run records which were present.
    runtime = {name: os.environ[name] for name in sorted({n for c in checks.values() for n in c.get('requires', ())})
               if os.environ.get(name)}
    env.update(runtime)
    started, timed_out, tree = time.time(), False, tree_identity()
    inputs, runtime_before = check_inputs(files), runtime_inputs(runtime)
    with (destination / 'run.log').open('wb') as log:
        child = subprocess.Popen(command, cwd=ROOT / 'services', env=env, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            code = child.returncode
    try:
        cases = junit_cases(junit)
    except (OSError, ET.ParseError):
        cases = []
    return seal_run({'kind': 'execution-v2', 'selected': sorted(checks), 'selectors': selectors,
                     'registration': checks,
                     'command': command, 'exit': code, 'timed_out': timed_out, 'started': started,
                     'finished': time.time(), 'junit': str(junit), 'cases': cases, 'tree': tree,
                     'tree_after': tree_identity(), 'inputs': inputs, 'inputs_after': check_inputs(files), 'runtime': runtime,
                     'runtime_inputs': runtime_before, 'runtime_inputs_after': runtime_inputs(runtime),
                     'execution': {'jobs': jobs, 'quiet_timing': quiet_timing, 'timeout': timeout}})


def check_inputs(files):
    """The runner, fixtures, inventory and test files the evidence names; tree_identity covers the rest."""
    helpers = ('scientific_comparison_recipes.py', 'scientific_reference_process.py',
               'scientific_reference_replay.py', 'scientific_reference_dependencies.json',
               'test_scientific_reference_comparison.py', 'scientific_browser_p2.mjs', 'scientific_document_scope.mjs', 'scientific_p2_observation.mjs',
               'scientific_parity.py', 'scientific_parity_reference.py', 'scientific_pixel_profile.py', 'scientific_napari_reference.py')
    paths = [Path(__file__), ROOT / 'services/tests/conftest.py', INVENTORY,
             *(ROOT / 'services/tests' / name for name in helpers),
             *(ROOT / 'services' / f for f in files)]
    if any('test_scientific_annotation_browser.py' in str(f) for f in files):
        paths.extend(ROOT / 'services/tests' / name for name in (
            'scientific_annotation_reference.py', 'scientific_annotation_api.py', 'scientific_annotation_fixture.py',
            'scientific_comparison_recipes.py', 'scientific_reference_dependencies.json',
            'test_scientific_reference_comparison.py', 'scientific_napari_reference.py'))
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def tree_identity():
    """The code under test: the commit, every tracked change and every untracked (not ignored) file."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    untracked = hashlib.sha256()
    for name in sorted(n for n in git('ls-files', '--others', '--exclude-standard', '-z').decode().split('\0') if n):
        path = ROOT / name
        untracked.update(name.encode() + b'\0' + (digest(path).encode() if path.is_file() else b'-') + b'\0')
    return {'commit': git('rev-parse', 'HEAD').decode().strip(),
            'tracked_changes_sha256': hashlib.sha256(git('diff', 'HEAD', '--binary')).hexdigest(),
            'untracked_sha256': untracked.hexdigest()}


def _seal_key(create=False):
    """A per-checkout local key; it seals reports against editing or import, not as authenticated evidence."""
    path = ROOT / 'reports/scientific-acceptance/.seal-key'
    if not path.is_file():
        if not create:
            return None
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(os.urandom(32))
    return path.read_bytes()


def _seal(run, key):
    import hmac
    body = json.dumps({k: v for k, v in run.items() if k != 'seal'}, sort_keys=True).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def seal_run(run):
    run = {**run, 'checkout': str(ROOT), 'runner_sha256': digest(Path(__file__)),
           **({'junit_sha256': digest(Path(run['junit'])) if Path(run['junit']).is_file() else None}
              if run.get('kind') != 'aggregate-v1' else {})}
    return {**run, 'seal': _seal(run, _seal_key(create=True))}


def reuse(previous, *, _leaf_only=False):
    """A retained run counts only if this checkout sealed it, nothing in it was edited, and the tree is unchanged."""
    import hmac
    previous = Path(previous)
    results = json.loads((previous / 'report.json').read_text()).get('run')
    key = _seal_key()
    if type(results) is not dict or key is None or not isinstance(results.get('seal'), str) \
            or not hmac.compare_digest(results['seal'], _seal(results, key)):
        raise InvalidAcceptance(f'{previous}: refused, an unsigned or edited report')
    if results.get('checkout') != str(ROOT) or results.get('runner_sha256') != digest(Path(__file__)):
        raise InvalidAcceptance(f'{previous}: refused, a report from another checkout or runner')
    if results.get('kind') == 'aggregate-v1':
        if _leaf_only:
            raise InvalidAcceptance(f'{previous}: refused, nested aggregates are not executions')
        refs = results.get('leaves')
        if not isinstance(refs, list) or not refs or len(refs) > 64:
            raise InvalidAcceptance(f'{previous}: refused, invalid aggregate members')
        admitted = aggregate([Path(leaf['receipt']).parent for leaf in refs], _nested=True)
        if admitted['leaves'] != refs or admitted['tree'] != results.get('tree'):
            raise InvalidAcceptance(f'{previous}: refused, original aggregate evidence changed')
        return results
    junit = Path(results['junit'])
    if not junit.is_file() or digest(junit) != results.get('junit_sha256'):
        raise InvalidAcceptance(f'{previous}: refused, its JUnit evidence is missing or changed')
    if results.get('kind') != 'execution-v2':
        raise InvalidAcceptance(f'{previous}: refused, not a current sealed execution')
    if problem := execution_problem(results):
        raise InvalidAcceptance(f'{previous}: refused, {problem}')
    if results.get('runtime_inputs') != runtime_inputs(results.get('runtime', {})):
        raise InvalidAcceptance(f'{previous}: refused, runtime configuration changed')
    now = tree_identity()
    changed = [k for k, v in now.items() if results.get('tree', {}).get(k) != v]
    if changed:
        return {**results, 'stale': f'stale evidence: the code under test changed since {previous} ({", ".join(changed)})'}
    for name, value in results['inputs'].items():
        path = ROOT / name
        if not path.is_file() or digest(path) != value:
            return {**results, 'stale': f'stale evidence: {name} changed since {previous}'}
    _validate_leaf(results, previous)
    return results


def _validate_leaf(run, previous):
    """A sealed receipt must still describe this runner's complete selected registrations."""
    selected = run.get('selected')
    if not isinstance(selected, list) or not selected or selected != sorted(set(selected)) or set(selected) - set(CHECKS):
        raise InvalidAcceptance(f'{previous}: refused, unknown or malformed selection')
    expected = {key: CHECKS[key] for key in selected}
    if json.dumps(run.get('registration'), sort_keys=True) != json.dumps(expected, sort_keys=True) \
            or run.get('selectors') != _selectors(expected):
        raise InvalidAcceptance(f'{previous}: refused, changed mandatory registrations')
    observed = junit_cases(Path(run['junit']))
    if observed != run.get('cases') or not observed:
        raise InvalidAcceptance(f'{previous}: refused, JUnit and sealed observations disagree or are empty')
    nodes = [case['node'] for case in observed]
    if len(nodes) != len(set(nodes)):
        raise InvalidAcceptance(f'{previous}: refused, duplicate JUnit test observations')
    if any(case['outcome'] != 'passed' for case in observed):
        raise InvalidAcceptance(f'{previous}: refused, failed or skipped execution evidence')
    for key, check in expected.items():
        if any(not any(_matches(node, test) for node in nodes) for test in check['tests']):
            raise InvalidAcceptance(f'{previous}: refused, mandatory checks did not execute for {key}')
        for name in check.get('requires', ()):
            if not run.get('runtime_inputs', {}).get(name, {}).get('sha256'):
                raise InvalidAcceptance(f'{previous}: refused, missing prepared runtime {name}')


def aggregate(previous, *, _nested=False):
    """Union original, current, locally sealed executions, keeping each child's guards together."""
    paths = [Path(path).resolve() for path in previous]
    if not paths or len(paths) > 64 or len(set(paths)) != len(paths):
        raise InvalidAcceptance('Aggregate needs 1–64 distinct original receipts; duplicate sources are refused')
    leaves, runtimes, observations = [], {}, {}
    tree = None
    for path in sorted(paths):
        run = reuse(path, _leaf_only=True)
        if tree is None:
            tree = run.get('tree')
        if run.get('stale') or run.get('tree') != tree:
            raise InvalidAcceptance(f'{path}: refused, stale evidence')
        for name, identity in run['runtime_inputs'].items():
            if name in runtimes and runtimes[name] != identity:
                raise InvalidAcceptance(f'{path}: refused, conflicting runtime configuration {name}')
            runtimes[name] = identity
        for case in run['cases']:
            if case['node'] in observations and observations[case['node']] != case['outcome']:
                raise InvalidAcceptance(f'{path}: refused, conflicting duplicate observation {case["node"]}')
            observations[case['node']] = case['outcome']
        leaves.append({'receipt': str(path / 'report.json'), 'run': run})
    if tree_identity() != tree:
        raise InvalidAcceptance('Source changed while aggregating evidence')
    value = {'kind': 'aggregate-v1', 'tree': tree, 'leaves': leaves}
    # Validation of an existing aggregate has no writing/key-creation side effects.
    return value if _nested else seal_run(value)


def source_verified(archive):
    """The committed inventory equals a fresh generation from the pinned archive."""
    from bench import scientific_inventory as census
    if archive is None or not Path(archive).is_file():
        return False, 'pinned napari archive not available here; the inventory was not regenerated'
    fresh = census.dump_atomic(census.atomic_cases(archive))
    if fresh != INVENTORY.read_text():
        return False, 'atomic-cases.json differs from regeneration: run make scientific-acceptance REGENERATE=1'
    return True, f'regenerated identically from {archive}'


def acceptance_report(inventory, results, *, verified=(False, 'not checked'), checks=None):
    children = {}
    for child in inventory['children']:
        children[child['id']] = {'group': child['group'], 'kind': child['kind'],
                                 'evidence_class': child['evidence_class'], **child_status(child, results, checks)}
    groups = {}
    for group, meta in inventory['groups'].items():
        states = [c['status'] for c in children.values() if c['group'] == group]
        groups[group] = {'status': group_status(states), 'owner': meta['owner'], 'title': meta['title'],
                         'counts': {s: states.count(s) for s in STATES}}
    census_summary = inventory['census']
    blockers = []
    if any(g['status'] != 'pass' for g in groups.values()):
        blockers.append(f'{sum(g["status"] != "pass" for g in groups.values())} of {len(groups)} groups are not passing')
    if census_summary['unreviewed']:
        blockers.append(f'{census_summary["unreviewed"]} pinned-source declarations are not yet reviewed')
    traced = {c['group'] for c in inventory['children'] if c.get('source')}
    untraced = sorted(set(inventory['groups']) - traced)
    if untraced:
        blockers.append(f'{len(untraced)} groups have no child traced to pinned napari source: {", ".join(untraced)}')
    for source in inventory.get('unpinned_sources', []):
        blockers.append(f'unpinned reference source: {source}')
    if not verified[0]:
        blockers.append(verified[1])
    blockers.append('no independent coverage review or authenticated qualification is recorded (H00/H14)')
    totals = Counter(c['status'] for c in children.values())
    return {'format': 1, 'class': 'acceptance_report', 'qualification_eligible': False, 'completion': False,
            'completion_blockers': blockers, 'source_verified': verified[0], 'source_note': verified[1],
            'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
            'ledger_sha256': digest(LEDGER), 'handoff_sha256': digest(BASELINE),
            'census': {k: v for k, v in census_summary.items() if k not in ('unreviewed_by_source', 'unreviewed_items')},
            'totals': {'children': {s: totals.get(s, 0) for s in STATES},
                       'groups': {s: sum(g['status'] == s for g in groups.values()) for s in STATES}},
            'run': results, 'groups': groups, 'children': children}


def write_report(report, destination):
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    (destination / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    lines = ['# Scientific acceptance report', '',
             f'Completion: **{report["completion"]}**. Blockers: ' + '; '.join(report['completion_blockers']) + '.', '',
             f'Children: {report["totals"]["children"]}. Groups: {report["totals"]["groups"]}.', '',
             f'Source: {report["source_note"]}. Census: {report["census"]}.', '',
             '| Group | Owner | Status | pass | fail | missing | external |', '| --- | --- | --- | --- | --- | --- | --- |']
    for group, info in report['groups'].items():
        c = info['counts']
        lines.append(f'| [{group}](#{group.lower().replace(".", "")}) | {info["owner"]} | {info["status"]} | '
                     f'{c["pass"]} | {c["fail"]} | {c["missing"]} | {c["external"]} |')
    for group, info in report['groups'].items():
        lines += ['', f'## {group}', '', f'{info["title"]} ({info["owner"]}): **{info["status"]}**', '']
        for ident, child in report['children'].items():
            if child['group'] != group:
                continue
            links = ', '.join(f'[{e["outcome"]}]({os.path.relpath(e["junit"], destination)}) `{e["test"]}`'
                              for e in child.get('evidence', [])[:3])
            more = len(child.get('evidence', [])) - 3
            lines.append(f'- `{ident}`: **{child["status"]}**: {child["reason"]}' + (f'. {links}' if links else '')
                         + (f' and {more} more' if more > 0 else ''))
    (destination / 'report.md').write_text('\n'.join(lines) + '\n')


def default_archive():
    reference = json.loads(LEDGER.read_text()).get('reference', {}).get('archive')
    return os.environ.get('NAPARI_ARCHIVE') or reference


def run(case_id, destination, *, timeout=600):
    if case_id not in REGRESSIONS:
        raise InvalidAcceptance('This case has no registered execution yet; it remains open')
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise InvalidAcceptance('Execution needs a bounded deadline of 1–3600 seconds')
    ledger = json.loads(LEDGER.read_text())
    summary = validate(ledger)
    regression_inventory()
    mapping = REGRESSIONS[case_id]
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    profile = destination/'profile'; profile.mkdir(mode=0o700)
    command = [sys.executable, '-m', 'pytest', '-q', *mapping['tests'],
               f'--junitxml={destination / "junit.xml"}']
    env = {key:os.environ[key] for key in ('PATH','TMPDIR','SYSTEMROOT') if key in os.environ}
    env.update(HOME=str(profile), MICROMIND_HOME=str(profile), MICROMIND_KEYCHAIN='off',
               MICROMIND_KEYS_FILE=str(profile/'absent-keys'), MICROMIND_CATALOG_AUTO='0',
               HF_HUB_OFFLINE='1', PYTHONUTF8='1')
    started = time.time()
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    diff = subprocess.check_output(['git','diff','HEAD','--'],cwd=ROOT)
    # Include untracked test/runner contents as well as commit identity.
    inputs = {str(Path(__file__).relative_to(ROOT)): digest(Path(__file__)),
              'services/tests/conftest.py': digest(ROOT/'services/tests/conftest.py'),
              **{'services/'+p:digest(ROOT/'services'/p) for p in mapping['tests']}}
    timed_out = False
    with (destination/'run.log').open('wb') as log:
        child = subprocess.Popen(command,cwd=ROOT/'services',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            code = child.returncode
    try:
        outcome = junit_outcome(destination/'junit.xml')
    except InvalidAcceptance as error:
        outcome = {'passed':False,'error':str(error)}
    report = {'format':1,'case':case_id,'class':'synthetic_fixture','qualification_eligible':False,
              'completion':False,'component_scope':mapping['scope'],'remaining':mapping['remaining'],
              'commit':commit,'working_diff_sha256':hashlib.sha256(diff).hexdigest(),'inputs':inputs,
              'ledger_sha256':digest(LEDGER),'handoff_sha256':digest(BASELINE),'command':command,
              'started':started,'finished':time.time(),'exit':code,'timed_out':timed_out,
              'result':outcome,'passed':code == 0 and not timed_out and outcome['passed'],
              'artifacts':{p.name:digest(p) for p in destination.iterdir() if p.is_file()},
              'ledger_summary':summary}
    (destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=sorted(REGRESSIONS), help='run one regression component (legacy receipt)')
    parser.add_argument('--list', action='store_true', help='list executable components and unmet obligations')
    parser.add_argument('--ledger', action='store_true', help='only validate the 71-group ledger')
    parser.add_argument('--output', type=Path, help='new report directory (default reports/scientific-acceptance/<time>)')
    parser.add_argument('--no-run', action='store_true', help='report without executing the registered checks')
    parser.add_argument('--from', dest='previous', type=Path, action='append',
                        help='reuse an original sealed report; repeat to aggregate same-source executions')
    parser.add_argument('--child', action='append', default=[], help='execute a registered child with all mandatory guards')
    parser.add_argument('--harness', action='append', default=[],
                        help='execute a trusted harness: h04, h07, compare-event/attribute/enum/contribution, browser-p2, remaining')
    parser.add_argument('--archive', type=Path, help='pinned napari archive used to verify the inventory')
    parser.add_argument('--regenerate', action='store_true', help='regenerate atomic-cases.json from the archive first')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--quiet-timing', action='store_true',
                        help='enable timing bounds in a coordinated quiet run (requires --jobs 1)')
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    if (args.child or args.harness) and (args.case or args.no_run or args.previous or args.list or args.ledger):
        parser.error('--child/--harness require a new registered-check execution')
    if args.previous and (args.case or args.no_run or args.list or args.ledger):
        parser.error('--from only reuses original reports, without execution or another mode')
    if args.quiet_timing and (args.jobs != 1 or args.case or args.no_run or args.previous or args.list or args.ledger):
        parser.error('--quiet-timing requires a new registered-check execution with --jobs 1')
    try:
        if args.list:
            if args.case or args.output: parser.error('--list cannot execute a case')
            print(json.dumps(regression_inventory(), indent=2))
            return 1 if args.require_complete else 0
        if args.case:
            if args.output is None: parser.error('--case requires a new --output directory')
            result = run(args.case, args.output)
            print(json.dumps(result, indent=2))
            return 0 if result['passed'] and not args.require_complete else 1
        ledger = json.loads(LEDGER.read_text())
        summary = validate(ledger)
        if args.ledger:
            print(json.dumps(summary, indent=2))
            return 0 if not args.require_complete or summary['completion'] else 1
        archive = args.archive or default_archive()
        if args.regenerate:
            from bench import scientific_inventory as census
            INVENTORY.write_text(census.dump_atomic(census.atomic_cases(archive)))
        inventory = load_inventory(ledger=ledger)
        regression_inventory()
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        destination = (args.output or ROOT / 'reports/scientific-acceptance' / stamp).resolve()
        if destination.exists() and any(destination.iterdir()):
            parser.error(f'{destination} already holds a report; choose a new --output')
        if args.previous:
            results = reuse(args.previous[0]) if len(args.previous) == 1 else aggregate(args.previous)
        elif args.no_run:
            results = None
        else:
            selected = select_checks(children=args.child, harnesses=args.harness)
            results = run_checks(destination / 'checks', checks=selected, jobs=args.jobs, quiet_timing=args.quiet_timing)
        report = acceptance_report(inventory, results, verified=source_verified(archive))
        report['ledger_summary'] = summary
        write_report(report, destination)
        for group, info in report['groups'].items():
            c = info['counts']
            print(f'{info["status"]:<8} {group:<34} pass {c["pass"]:>3}  fail {c["fail"]:>3}  missing {c["missing"]:>4}  external {c["external"]:>3}')
        print(json.dumps({'totals': report['totals'], 'completion': report['completion'],
                          'completion_blockers': report['completion_blockers'],
                          'report': str(destination / 'report.md')}, indent=2))
        failed = report['totals']['children']['fail'] > 0
        return 1 if failed or (args.require_complete and not report['completion']) else 0
    except (InvalidAcceptance, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

CREATE TABLE `highlight_profiles` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`slug` text NOT NULL,
	`description` text,
	`game` text,
	`content_type` text,
	`status` text DEFAULT 'draft' NOT NULL,
	`active_version_id` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `highlight_profiles_slug_unique` ON `highlight_profiles` (`slug`);
--> statement-breakpoint
CREATE TABLE `highlight_profile_versions` (
	`id` text PRIMARY KEY NOT NULL,
	`profile_id` text NOT NULL,
	`version_number` integer NOT NULL,
	`config_json` text NOT NULL,
	`model_type` text DEFAULT 'config_only' NOT NULL,
	`model_artifact_path` text,
	`metrics_json` text,
	`training_dataset_id` text,
	`is_active` integer DEFAULT false NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`profile_id`) REFERENCES `highlight_profiles`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `highlight_profile_versions_profile_idx` ON `highlight_profile_versions` (`profile_id`);
--> statement-breakpoint
CREATE TABLE `training_datasets` (
	`id` text PRIMARY KEY NOT NULL,
	`profile_id` text NOT NULL,
	`name` text NOT NULL,
	`description` text,
	`source_notes` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`profile_id`) REFERENCES `highlight_profiles`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `training_datasets_profile_idx` ON `training_datasets` (`profile_id`);
--> statement-breakpoint
CREATE TABLE `reference_clips` (
	`id` text PRIMARY KEY NOT NULL,
	`dataset_id` text NOT NULL,
	`source_type` text NOT NULL,
	`source_url` text,
	`source_video_id` text,
	`file_path` text NOT NULL,
	`title` text,
	`duration_seconds` real,
	`start_time_in_source` real,
	`end_time_in_source` real,
	`labels_json` text,
	`metadata_json` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`dataset_id`) REFERENCES `training_datasets`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `reference_clips_dataset_idx` ON `reference_clips` (`dataset_id`);
--> statement-breakpoint
CREATE TABLE `training_examples` (
	`id` text PRIMARY KEY NOT NULL,
	`dataset_id` text NOT NULL,
	`reference_clip_id` text,
	`project_id` text,
	`source_video_id` text,
	`start_seconds` real NOT NULL,
	`end_seconds` real NOT NULL,
	`label` text NOT NULL,
	`confidence` real,
	`reason` text NOT NULL,
	`features_json` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`dataset_id`) REFERENCES `training_datasets`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`reference_clip_id`) REFERENCES `reference_clips`(`id`) ON UPDATE no action ON DELETE set null,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE INDEX `training_examples_dataset_idx` ON `training_examples` (`dataset_id`);
--> statement-breakpoint
CREATE INDEX `training_examples_label_idx` ON `training_examples` (`label`);
--> statement-breakpoint
CREATE TABLE `extracted_feature_windows` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text,
	`reference_clip_id` text,
	`start_seconds` real NOT NULL,
	`end_seconds` real NOT NULL,
	`window_size_seconds` real NOT NULL,
	`features_json` text,
	`transcript_text` text,
	`chat_features_json` text,
	`audio_features_json` text,
	`visual_features_json` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`reference_clip_id`) REFERENCES `reference_clips`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `extracted_feature_windows_project_idx` ON `extracted_feature_windows` (`project_id`);
--> statement-breakpoint
CREATE INDEX `extracted_feature_windows_reference_idx` ON `extracted_feature_windows` (`reference_clip_id`);
--> statement-breakpoint
CREATE TABLE `profile_training_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`profile_id` text NOT NULL,
	`dataset_id` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`optimizer` text DEFAULT 'optuna' NOT NULL,
	`params_json` text,
	`result_config_json` text,
	`metrics_json` text,
	`logs_path` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`completed_at` integer,
	FOREIGN KEY (`profile_id`) REFERENCES `highlight_profiles`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`dataset_id`) REFERENCES `training_datasets`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `profile_training_runs_profile_idx` ON `profile_training_runs` (`profile_id`);
--> statement-breakpoint
CREATE TABLE `profile_scored_candidates` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`profile_version_id` text NOT NULL,
	`start_seconds` real NOT NULL,
	`end_seconds` real NOT NULL,
	`score` real NOT NULL,
	`signal_breakdown_json` text,
	`title_suggestion` text,
	`explanation` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`profile_version_id`) REFERENCES `highlight_profile_versions`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `profile_scored_candidates_project_idx` ON `profile_scored_candidates` (`project_id`);
--> statement-breakpoint
INSERT INTO `highlight_profiles` (`id`, `name`, `slug`, `description`, `game`, `content_type`, `status`, `created_at`, `updated_at`)
VALUES (
	'valorant_reaction',
	'Valorant Reaction Shorts',
	'valorant_reaction_shorts',
	'Optimized for Valorant streamer/reactor YouTube Shorts — clutch plays, aces, and reaction moments.',
	'valorant',
	'reaction_shorts',
	'active',
	unixepoch() * 1000,
	unixepoch() * 1000
);
--> statement-breakpoint
INSERT INTO `highlight_profile_versions` (`id`, `profile_id`, `version_number`, `config_json`, `model_type`, `is_active`, `created_at`)
VALUES (
	'valorant_reaction_v1',
	'valorant_reaction',
	1,
	'{"metadata":{"name":"Valorant Reaction Shorts","slug":"valorant_reaction_shorts","game":"valorant","contentType":"reaction_shorts"},"candidateSources":{"audioPeaks":true,"transcriptKeywords":true,"semanticPhrases":true,"chatBursts":true,"sceneCuts":true,"ocrEvents":false},"timing":{"minDurationSeconds":20,"targetDurationSeconds":45,"maxDurationSeconds":60,"preRollSeconds":8,"postRollSeconds":2,"mergeWindowSeconds":12,"dedupeOverlapThreshold":0.55},"keywords":{"ace":1.0,"clutch":0.95,"one tap":0.9,"four kill":0.9,"quad":0.85,"insane":0.8,"no way":0.85,"what":0.6,"flawless":0.85,"spike":0.7,"planted":0.65,"defuse":0.7,"last alive":0.9,"he''s one":0.85,"team ace":0.95},"phrases":["no way","what","insane","ace","clutch","one tap","four kill","he''s one","last alive","spike planted","flawless","team ace","let''s go","holy"],"scoreWeights":{"audioPeak":0.28,"keyword":0.22,"semanticPhrase":0.18,"chatBurst":0.15,"scene":0.08,"ocr":0.05},"thresholds":{"audioPeakMin":0.55,"chatBurstMin":0.5,"embeddingSimilarityMin":0.62,"sceneCutBonus":0.15},"penalties":{"duplicate":0.25,"tooShort":0.3,"tooLong":0.2,"weakTranscript":0.15},"normalization":{"audioZScoreCap":3.0,"chatZScoreCap":3.0},"renderDefaults":{"aspect":"9:16","preRollSeconds":8}}',
	'config_only',
	1,
	unixepoch() * 1000
);
--> statement-breakpoint
UPDATE `highlight_profiles` SET `active_version_id` = 'valorant_reaction_v1' WHERE `id` = 'valorant_reaction';

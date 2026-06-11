CREATE TABLE `ranking_preferences` (
	`id` text PRIMARY KEY NOT NULL DEFAULT 'default',
	`weights_json` text NOT NULL,
	`learned_pre_roll_seconds` real DEFAULT 8 NOT NULL,
	`learned_tail_padding_seconds` real DEFAULT 2 NOT NULL,
	`editor_pad_before_seconds` real DEFAULT 10 NOT NULL,
	`editor_pad_after_seconds` real DEFAULT 10 NOT NULL,
	`feedback_count` integer DEFAULT 0 NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL
);
--> statement-breakpoint
INSERT INTO `ranking_preferences` (`id`, `weights_json`, `learned_pre_roll_seconds`, `learned_tail_padding_seconds`, `editor_pad_before_seconds`, `editor_pad_after_seconds`, `feedback_count`)
VALUES (
	'default',
	'{"fusionVisual":0.24,"fusionChat":0.18,"fusionAudio":0.16,"fusionTranscript":0.14,"fusionAlignment":0.10,"fusionScene":0.10,"fusionAgreement":0.08,"candidateAudio":0.75,"candidateChat":0.40,"candidateKeyword":0.25,"candidateChatAudio":0.45,"geminiBlendLlm":0.55,"geminiBlendLocal":0.45}',
	8,
	2,
	10,
	10,
	0
);
--> statement-breakpoint
CREATE TABLE `clip_feedback` (
	`id` text PRIMARY KEY NOT NULL,
	`clip_id` text NOT NULL,
	`highlight_id` text NOT NULL,
	`project_id` text NOT NULL,
	`overall_vote` text,
	`signal_votes_json` text,
	`effective_pre_roll_seconds` real,
	`effective_tail_seconds` real,
	`highlight_start_seconds` real,
	`highlight_end_seconds` real,
	`source_start_seconds` real,
	`source_end_seconds` real,
	`reason_snapshot_json` text,
	`notes` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`clip_id`) REFERENCES `clips`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`highlight_id`) REFERENCES `highlights`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `clip_feedback_clip_idx` ON `clip_feedback` (`clip_id`);
--> statement-breakpoint
CREATE INDEX `clip_feedback_project_idx` ON `clip_feedback` (`project_id`);

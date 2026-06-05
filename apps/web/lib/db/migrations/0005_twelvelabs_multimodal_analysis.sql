CREATE TABLE `external_video_indexes` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`video_id` text NOT NULL,
	`provider` text NOT NULL,
	`provider_index_id` text,
	`provider_video_id` text,
	`provider_task_id` text,
	`status` text DEFAULT 'pending' NOT NULL,
	`source_path` text,
	`source_sha256` text,
	`duration_seconds` real,
	`chunk_index` integer DEFAULT 0,
	`chunk_start_seconds` real DEFAULT 0,
	`chunk_end_seconds` real,
	`metadata_json` text,
	`error_message` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`video_id`) REFERENCES `videos`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_external_video_indexes_project_provider` ON `external_video_indexes` (`project_id`,`provider`);
--> statement-breakpoint
CREATE TABLE `visual_segments` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`video_id` text NOT NULL,
	`provider` text NOT NULL,
	`model` text,
	`source_method` text NOT NULL,
	`start_seconds` real NOT NULL,
	`end_seconds` real NOT NULL,
	`segment_type` text,
	`confidence` real,
	`title` text,
	`description` text,
	`visual_reason` text,
	`audio_reason` text,
	`speech_reason` text,
	`chat_reason` text,
	`raw_json` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`video_id`) REFERENCES `videos`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_visual_segments_project_time` ON `visual_segments` (`project_id`,`start_seconds`,`end_seconds`);
--> statement-breakpoint
CREATE INDEX `idx_visual_segments_project_type` ON `visual_segments` (`project_id`,`segment_type`);
--> statement-breakpoint
CREATE TABLE `highlight_candidates` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`video_id` text NOT NULL,
	`source` text NOT NULL,
	`start_seconds` real NOT NULL,
	`end_seconds` real NOT NULL,
	`seed_source` text,
	`moment_type` text,
	`confidence` real,
	`score` real DEFAULT 0 NOT NULL,
	`local_score` real,
	`transcript_score` real,
	`audio_score` real,
	`chat_score` real,
	`scene_score` real,
	`visual_score` real,
	`multimodal_score` real,
	`fusion_score` real,
	`audio_peak_at` real,
	`chat_peak_at` real,
	`visual_peak_at` real,
	`title` text,
	`summary` text,
	`reason_json` text,
	`raw_provider_json` text,
	`selected_for_rerank` integer DEFAULT 0 NOT NULL,
	`selected_as_highlight` integer DEFAULT 0 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`video_id`) REFERENCES `videos`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_highlight_candidates_project_score` ON `highlight_candidates` (`project_id`,`score`);
--> statement-breakpoint
CREATE INDEX `idx_highlight_candidates_project_time` ON `highlight_candidates` (`project_id`,`start_seconds`,`end_seconds`);

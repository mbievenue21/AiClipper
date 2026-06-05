CREATE TABLE `pipeline_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`status` text DEFAULT 'running' NOT NULL,
	`started_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`finished_at` integer,
	`video_duration_seconds` real,
	`twelvelabs_enabled` integer DEFAULT 0 NOT NULL,
	`is_reanalysis` integer DEFAULT 0 NOT NULL,
	`meta_json` text,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `pipeline_runs_project_idx` ON `pipeline_runs` (`project_id`);
--> statement-breakpoint
CREATE INDEX `pipeline_runs_started_idx` ON `pipeline_runs` (`started_at`);
--> statement-breakpoint
CREATE TABLE `pipeline_stage_timings` (
	`id` text PRIMARY KEY NOT NULL,
	`run_id` text NOT NULL,
	`project_id` text NOT NULL,
	`stage` text NOT NULL,
	`duration_ms` integer NOT NULL,
	`started_at` integer,
	`finished_at` integer,
	`status` text DEFAULT 'ok' NOT NULL,
	`job_id` text,
	`meta_json` text,
	FOREIGN KEY (`run_id`) REFERENCES `pipeline_runs`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `pipeline_stage_timings_run_idx` ON `pipeline_stage_timings` (`run_id`);
--> statement-breakpoint
CREATE INDEX `pipeline_stage_timings_project_stage_idx` ON `pipeline_stage_timings` (`project_id`, `stage`);

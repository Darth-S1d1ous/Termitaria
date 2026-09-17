package bus

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/nats-io/nats.go/jetstream"
)

// 3 stream definitions
var streamDefs = []jetstream.StreamConfig{
	{
		Name: 		"SESSIONS",
		Subjects: 	[]string{"sessions.>"},
		Retention: 	jetstream.LimitsPolicy,
		Storage:  	jetstream.FileStorage,
		Replicas: 	1,
		Duplicates: 2 * time.Minute,
	},
	{
		Name: 		"TASKS",
		Subjects: 	[]string{"tasks.*.*"},
		Retention: 	jetstream.WorkQueuePolicy,
		Storage: 	jetstream.FileStorage,
		Replicas: 	1,
	},
	{
		Name: 		"MEMORY",
		Subjects: 	[]string{"memory.write.>"},
		Retention: 	jetstream.LimitsPolicy,
		Storage: 	jetstream.FileStorage,
		Replicas: 	1,
		Duplicates: 2 * time.Minute,
	},
}

// if exists, update; if not, create
func (b *Bus) EnsureStreams(ctx context.Context) error {
	for _, def := range streamDefs {
		_, err := b.js.Stream(ctx, def.Name)
		switch {
		case errors.Is(err, jetstream.ErrStreamNotFound):
			if _, err := b.js.CreateStream(ctx, def); err != nil {
				return fmt.Errorf("create stream %s: %w", def.Name, err)
			}
		case err != nil:
			return fmt.Errorf("get stream %s: %w", def.Name, err)
		default:
			if _, err := b.js.UpdateStream(ctx, def); err != nil {
				return fmt.Errorf("update stream %s: %w", def.Name, err)
			}
		}
	}
	return nil
}
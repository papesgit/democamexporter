package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	dem "github.com/markus-wa/demoinfocs-golang/v4/pkg/demoinfocs"
	"github.com/markus-wa/demoinfocs-golang/v4/pkg/demoinfocs/common"
	"github.com/markus-wa/demoinfocs-golang/v4/pkg/demoinfocs/events"
)

var (
	currentRound  = 1
	currentFile   *os.File
	currentWriter *csv.Writer
	lastTick      int
	outputFolder  string
	splitRounds   bool
	baseWriter    *csv.Writer
	baseFile      *os.File
)

func main() {
	// Command-line flags
	demoPath := flag.String("demo", "protestdemo.dem", "Path to the demo file")
	flag.BoolVar(&splitRounds, "split-rounds", false, "If true, split output per round into separate CSV files")
	flag.Parse()

	// Prepare output folder name (based on demo file, without extension)
	baseName := strings.TrimSuffix(filepath.Base(*demoPath), filepath.Ext(*demoPath))
	outputFolder = baseName

	err := os.MkdirAll(outputFolder, os.ModePerm)
	if err != nil {
		log.Fatalf("❌ Failed to create output folder: %v", err)
	}

	f, err := os.Open(*demoPath)
	if err != nil {
		log.Fatal("❌ Failed to open demo:", err)
	}
	defer f.Close()

	p := dem.NewParser(f)

	// If not splitting rounds, open a single CSV upfront
	if !splitRounds {
		baseFile, baseWriter = openCSV(filepath.Join(outputFolder, "all_ticks.csv"))
		defer closeCSV(baseFile, baseWriter)
	}

	// Register handlers
	p.RegisterEventHandler(func(e events.RoundStart) {
		if splitRounds {
			startNewRound()
		}
	})

	p.RegisterEventHandler(func(e events.FrameDone) {
		gs := p.GameState()
		tick := gs.IngameTick()

		// Avoid duplicate ticks
		if tick == lastTick {
			return
		}
		lastTick = tick

		for _, player := range gs.Participants().Playing() {
			if splitRounds && currentWriter != nil {
				writePlayerData(currentWriter, tick, player)
			} else if !splitRounds && baseWriter != nil {
				writePlayerData(baseWriter, tick, player)
			}
		}
	})

	// Parse the demo
	err = p.ParseToEnd()
	if err != nil {
		log.Fatalf("❌ Error during parsing: %v", err)
	}

	// Final cleanup
	if splitRounds {
		closeCurrentRound()
	}

	fmt.Printf("✅ Done! Output written to folder: %s\n", outputFolder)
}

func startNewRound() {
	// Close previous round file if open
	closeCurrentRound()

	// Build file path in the output folder
	filename := fmt.Sprintf("round_%d.csv", currentRound)
	fullPath := filepath.Join(outputFolder, filename)

	file, writer := openCSV(fullPath)
	currentFile = file
	currentWriter = writer

	fmt.Printf("➡️  Started round %d → writing to %s\n", currentRound, fullPath)

	currentRound++
}

func closeCurrentRound() {
	closeCSV(currentFile, currentWriter)
	currentFile = nil
	currentWriter = nil
}

func openCSV(path string) (*os.File, *csv.Writer) {
	file, err := os.Create(path)
	if err != nil {
		log.Fatalf("❌ Failed to create CSV file: %v", err)
	}
	writer := csv.NewWriter(file)
	writer.Write([]string{
		"tick", "player_name",
		"pos_x", "pos_y", "pos_z",
		"view_dir_x", "view_dir_y",
	})
	return file, writer
}

func closeCSV(file *os.File, writer *csv.Writer) {
	if writer != nil {
		writer.Flush()
	}
	if file != nil {
		file.Close()
	}
}

func writePlayerData(writer *csv.Writer, tick int, player *common.Player) {
	pos := player.Position()

	writer.Write([]string{
		strconv.Itoa(tick),
		player.Name,
		fmt.Sprintf("%.2f", pos.X),
		fmt.Sprintf("%.2f", pos.Y),
		fmt.Sprintf("%.2f", pos.Z),
		fmt.Sprintf("%.4f", player.ViewDirectionX()),
		fmt.Sprintf("%.4f", player.ViewDirectionY()),
	})
}

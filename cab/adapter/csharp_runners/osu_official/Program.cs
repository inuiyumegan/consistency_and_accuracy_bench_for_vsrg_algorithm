// osu!lazer official mania star rating runner for CAB-VA.
//
// Computes the *official* osu!lazer mania difficulty (star rating) for a .osu
// beatmap by calling into osu.Game, which is referenced as a project and built
// from a local checkout of https://github.com/ppy/osu (master).  The numbers
// therefore come from the same code path osu!lazer itself uses for its
// official mania star rating.  No mods are applied.
//
// Two modes (the persistent-server protocol mirrors the benchmark's spec):
//
//   1) One-shot:  dotnet osu_official.dll <beatmap.osu>
//        prints a single float on stdout
//
//   2) Server:    dotnet osu_official.dll --server
//        stdin lines:  {"id": 1, "file": "/abs/path/to/map.osu"}
//        stdout lines: {"id": 1, "difficulty": 8.42, "status": "ok"}
//                      {"id": 1, "difficulty": null, "status": "error", "error": "..."}

using System;
using System.Globalization;
using System.IO;
using System.Text.Json;
using osu.Framework.Audio.Track;
using osu.Framework.Graphics.Textures;
using osu.Framework.Logging;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Formats;
using osu.Game.IO;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Mania;
using osu.Game.Skinning;

namespace CabOsuOfficial
{
    internal static class Program
    {
        private static readonly ManiaRuleset Mania = new ManiaRuleset();

        private static int Main(string[] args)
        {
            Logger.Enabled = false;

            if (args.Length == 1 && args[0] == "--server")
                return RunServer();

            if (args.Length == 1)
            {
                Console.WriteLine(Calculate(args[0]).ToString("R", CultureInfo.InvariantCulture));
                return 0;
            }

            Console.Error.WriteLine("usage: dotnet osu_official.dll <beatmap.osu> | --server");
            return 2;
        }

        private static int RunServer()
        {
            string? line;
            while ((line = Console.In.ReadLine()) != null)
            {
                if (string.IsNullOrWhiteSpace(line))
                    continue;

                long id = -1;
                try
                {
                    using var doc = JsonDocument.Parse(line);
                    var root = doc.RootElement;
                    id = root.TryGetProperty("id", out var idProp) ? idProp.GetInt64() : -1;
                    string file = root.GetProperty("file").GetString()!;
                    double difficulty = Calculate(file);
                    Console.WriteLine(JsonSerializer.Serialize(new
                    {
                        id,
                        difficulty,
                        status = "ok",
                    }));
                }
                catch (Exception e)
                {
                    Console.WriteLine(JsonSerializer.Serialize(new
                    {
                        id,
                        difficulty = (double?)null,
                        status = "error",
                        error = e.Message,
                    }));
                }

                Console.Out.Flush();
            }

            return 0;
        }

        private static double Calculate(string file)
        {
            var beatmap = ReadBeatmap(file);
            var working = new CabWorkingBeatmap(beatmap, Mania.RulesetInfo);
            return Mania.CreateDifficultyCalculator(working).Calculate().StarRating;
        }

        private static Beatmap ReadBeatmap(string path)
        {
            using var stream = File.OpenRead(path);
            using var reader = new LineBufferedReader(stream);
            return Decoder.GetDecoder<Beatmap>(reader).Decode(reader);
        }
    }

    /// <summary>
    /// Minimal <see cref="WorkingBeatmap"/> over an already-decoded beatmap.
    /// Adapted from osu-tools' ProcessorWorkingBeatmap (MIT licensed).
    /// </summary>
    internal sealed class CabWorkingBeatmap : WorkingBeatmap
    {
        private readonly Beatmap beatmap;

        public CabWorkingBeatmap(Beatmap beatmap, RulesetInfo rulesetInfo)
            : base(beatmap.BeatmapInfo, null)
        {
            this.beatmap = beatmap;
            beatmap.BeatmapInfo.Ruleset = rulesetInfo;
        }

        protected override IBeatmap GetBeatmap() => beatmap;
        public override Texture GetBackground() => throw new NotImplementedException();
        protected override Track GetBeatmapTrack() => throw new NotImplementedException();
        protected override ISkin GetSkin() => throw new NotImplementedException();
        public override Stream GetStream(string storagePath) => throw new NotImplementedException();
    }
}

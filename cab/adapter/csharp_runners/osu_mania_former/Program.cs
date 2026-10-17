// "osu-mania-former" runner for CAB-VA.
//
// Computes the mania star rating produced by the official osu!lazer mania
// difficulty algorithm **as it stood immediately before** ppy/osu#24109
// ("Adjust LN value scaling to bring overrated LN maps more in line",
// merged 2023-08-17).  The referenced projects are pinned to tag
// 2023.815.0 — the last release cut before that merge — so the LN scaling
// in this binary is the pre-fix one:
//
//   release_threshold            = 24   (post-fix: 30)
//   holdAddition logistic slope  = 0.5  (post-fix: 0.27)
//   holdFactor / isOverlapping   = no `startTime > startTimes[i]` clause
//
// Everything else (rice note strain, decay bases, star-rating aggregation) is
// the 2023.815.0 code as-is; this is NOT today's algorithm with three
// constants rolled back.
//
// Two modes (the persistent-server protocol mirrors the benchmark's spec):
//
//   1) One-shot:  dotnet osu_mania_former.dll <beatmap.osu>
//        prints a single float on stdout
//
//   2) Server:    dotnet osu_mania_former.dll --server
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

namespace CabOsuManiaFormer
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

            Console.Error.WriteLine("usage: dotnet osu_mania_former.dll <beatmap.osu> | --server");
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
            var working = new FormerWorkingBeatmap(beatmap, Mania.RulesetInfo);
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
    /// Adapted from osu-tools' ProcessorWorkingBeatmap (MIT licensed), matched
    /// to the 2023.815.0 API surface.
    /// </summary>
    internal sealed class FormerWorkingBeatmap : WorkingBeatmap
    {
        private readonly Beatmap beatmap;

        public FormerWorkingBeatmap(Beatmap beatmap, RulesetInfo rulesetInfo)
            : base(beatmap.BeatmapInfo, null)
        {
            this.beatmap = beatmap;
            beatmap.BeatmapInfo.Ruleset = rulesetInfo;
        }

        protected override IBeatmap GetBeatmap() => beatmap;
        public override Texture GetBackground() => throw new NotImplementedException();
        protected override Track GetBeatmapTrack() => throw new NotImplementedException();
        // Base member is `protected internal`; an override from another
        // assembly must widen down to `protected`.
        protected override ISkin GetSkin() => throw new NotImplementedException();        public override Stream GetStream(string storagePath) => throw new NotImplementedException();
    }
}

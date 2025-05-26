import discord
from discord.ext import commands
from discord.ui import View, Button
import yt_dlp
import asyncio
import random
from ytmusicapi import YTMusic
from youtubesearchpython import VideosSearch

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

queue = []  # [{url, title}]
current_volume = 0.5
loop_enabled = False  # ループ再生の有効/無効

class YTDLSource:
    @staticmethod
    def get_info(url, process=False):
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'extract_flat': not process,
            'noplaylist': False
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

class QueueView(View):
    def __init__(self, ctx, queue, items_per_page=10):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.queue = queue
        self.items_per_page = items_per_page
        self.current_page = 0
        self.total_pages = max((len(queue) - 1) // items_per_page + 1, 1)

        self.message = None

        self.prev_button = Button(label="⬅️", style=discord.ButtonStyle.primary)
        self.next_button = Button(label="➡️", style=discord.ButtonStyle.primary)

        self.prev_button.callback = self.previous_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def get_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page

        # Embed本体
        embed = discord.Embed(title="🎵 再生キュー", color=0x1DB954)

        # 各曲をMarkdownリンクで埋め込んだ文字列にする
        description_lines = []
        for i, item in enumerate(self.queue[start:end], start=start + 1):
            title = item['title']
            url = item['url']
            description_lines.append(f"{i}. [{title}]({url})")

        embed.description = "\n".join(description_lines)

        # ページ番号を右上に表示
        embed.set_author(name=f"{self.current_page + 1}/{self.total_pages}")

        return embed

    async def previous_page(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % self.total_pages
        embed = self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % self.total_pages
        embed = self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

class PlaybackControlView(discord.ui.View):
    def __init__(self, ctx, url, title):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.url = url
        self.title = title

        # トグル用初期状態のボタンを追加（あとで置換される）
        self.toggle_button = discord.ui.Button(label="⏸ 一時停止", style=discord.ButtonStyle.danger)
        self.toggle_button.callback = self.toggle_pause_resume
        self.add_item(self.toggle_button)

    async def toggle_pause_resume(self, interaction: discord.Interaction):
        vc = self.ctx.voice_client
        if vc:
            if vc.is_playing():
                vc.pause()
                self.toggle_button.label = "▶️ 再開"
                self.toggle_button.style = discord.ButtonStyle.success
                await interaction.response.edit_message(content="⏸ 一時停止しました。", view=self)
            elif vc.is_paused():
                vc.resume()
                self.toggle_button.label = "⏸ 一時停止"
                self.toggle_button.style = discord.ButtonStyle.danger
                await interaction.response.edit_message(content="▶️ 再開しました。", view=self)
            else:
                await interaction.response.send_message("🎵 再生していません。", ephemeral=True)
        else:
            await interaction.response.send_message("🔇 ボイスチャンネルに接続されていません。", ephemeral=True)

    @discord.ui.button(label="🔉 -10%", style=discord.ButtonStyle.primary)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_volume
        current_volume = max(0.0, current_volume - 0.1)
        if self.ctx.voice_client and self.ctx.voice_client.source:
            self.ctx.voice_client.source.volume = current_volume
        await interaction.response.send_message(f"🔉 音量 {int(current_volume * 100)}%", ephemeral=True)

    @discord.ui.button(label="🔊 +10%", style=discord.ButtonStyle.primary)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_volume
        current_volume = min(1.0, current_volume + 0.1)
        if self.ctx.voice_client and self.ctx.voice_client.source:
            self.ctx.voice_client.source.volume = current_volume
        await interaction.response.send_message(f"🔊 音量 {int(current_volume * 100)}%", ephemeral=True)

    @discord.ui.button(label="⏭ 次の曲", style=discord.ButtonStyle.primary)
    async def next_track(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client and self.ctx.voice_client.is_playing():
            self.ctx.voice_client.stop()
            await interaction.response.send_message("⏭ 次の曲へスキップしました。", ephemeral=True)
        else:
            await interaction.response.send_message("🎵 再生中の曲がありません。", ephemeral=True)

def search_music_link(song_query: str) -> str | None:
    """
    まずYouTube MusicからMVを除外して検索し、見つからなければ通常のYouTubeから取得する。
    """
    # --- Step 1: Try ytmusicapi (filter="songs")
    try:
        ytmusic = YTMusic()
        results = ytmusic.search(song_query, filter="songs")
        for song in results:
            if song.get("videoId"):
                return f"https://www.youtube.com/watch?v={song['videoId']}"
    except Exception as e:
        print(f"[ytmusicapi error] {e}")

    # --- Step 2: Fallback to YouTube search
    try:
        search = VideosSearch(song_query, limit=5)
        for result in search.result().get("result", []):
            if "videoId" in result:
                return f"https://www.youtube.com/watch?v={result['id']}"
    except Exception as e:
        print(f"[youtube-search-python error] {e}")

    # --- Step 3: Nothing found
    return None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

async def play_next(ctx):
    if not ctx.voice_client:
        return  # 接続が切れている場合は何もせず終了

    if loop_enabled and ctx.voice_client.source:
        ctx.voice_client.stop()
        return

    if queue:
        next_track = queue.pop(0)
        url = next_track["url"]
        try:
            info = YTDLSource.get_info(url, process=True)
            title = info.get('title', 'Unknown Title')
            webpage_url = info.get('webpage_url', url)
            thumbnail = info.get('thumbnail', '')
            audio_url = info['url']

            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(audio_url), volume=current_volume)

            def after_play(err):
                fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    print(f"Playback error: {e}")

            if ctx.voice_client:  # 念のため再チェック
                ctx.voice_client.play(source, after=after_play)

            embed = discord.Embed(title="Now Playing 🎶", description=f"[{title}]({webpage_url})", color=0x1DB954)
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            view = PlaybackControlView(ctx, url, title)
            await ctx.send(embed=embed, view=view)

            if loop_enabled:
                queue.insert(0, next_track)

        except Exception as e:
            await ctx.send(f"Failed to play the URL: {e}")
            await play_next(ctx)
    else:
        await asyncio.sleep(180)
        if ctx.voice_client and not ctx.voice_client.is_playing():
            await ctx.voice_client.disconnect()

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await ctx.send("Joined voice channel.")
    else:
        await ctx.send("You're not in a voice channel.")

@bot.command()
async def play(ctx, *, args: str):
    if not ctx.voice_client:
        await ctx.invoke(bot.get_command("join"))

    parts = args.rsplit(" ", 1)
    query = parts[0]
    position = None

    if len(parts) == 2 and parts[1].isdigit():
        position = int(parts[1])
        query = parts[0]

    if not query.startswith("http://") and not query.startswith("https://"):
        await ctx.send(f"🔎 検索中: {query}")
        resolved_url = search_music_link(query)
        if resolved_url is None:
            await ctx.send("❌ 曲が見つかりませんでした。")
            return
        url = resolved_url
    else:
        url = query

    try:
        info = YTDLSource.get_info(url, process=False)

        if 'entries' in info:
            count = 0
            for entry in info['entries']:
                if entry and 'id' in entry:
                    video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    title = entry.get("title", "Unknown Title")
                    item = {"url": video_url, "title": title}
                    if position and 1 <= position <= len(queue):
                        queue.insert(position - 1, item)
                    else:
                        queue.append(item)
                    count += 1
            await ctx.send(f"📥 {count}件の動画をキューに追加しました。")
        else:
            title = info.get("title", "Unknown Title")
            item = {"url": url, "title": title}
            if position and 1 <= position <= len(queue):
                queue.insert(position - 1, item)
                await ctx.send(f"📥 {title} を {position} 番目に追加しました。")
            else:
                queue.append(item)
                await ctx.send(f"📥 キューに追加: {title}")

        if not ctx.voice_client.is_playing():
            await play_next(ctx)

    except Exception as e:
        await ctx.send(f"❌ エラーが発生しました: {e}")

@bot.command(name="remove")
async def remove(ctx, index: int):
    if 1 <= index <= len(queue):
        removed = queue.pop(index - 1)
        await ctx.send(f"Removed from queue: {removed['title']}")
    else:
        await ctx.send("Invalid index.")

@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        await ctx.send("The queue is empty.")
        return

    view = QueueView(ctx, queue)
    embed = view.get_embed()
    view.message = await ctx.send(embed=embed, view=view)

@bot.command()
async def skip(ctx, index: int = None):
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("Nothing is playing.")
        return

    if index is None:
        ctx.voice_client.stop()
        await ctx.send("Skipped to the next track.")
    else:
        if 1 <= index <= len(queue):
            skipped = queue.pop(index - 1)
            queue.insert(0, skipped)
            ctx.voice_client.stop()
            await ctx.send(f"Skipped to: {skipped['title']}")
        else:
            await ctx.send("Invalid index.")

@bot.command()
async def loop(ctx):
    global loop_enabled
    loop_enabled = not loop_enabled
    await ctx.send(f"Loop is now {'enabled 🔁' if loop_enabled else 'disabled'}.")

@bot.command()
async def shuffle(ctx):
    if queue:
        random.shuffle(queue)
        await ctx.send("Queue shuffled 🔀")
    else:
        await ctx.send("Queue is empty.")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")

@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("Stopped playback.")

@bot.command(name="disconnect")
async def disconnect(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        queue.clear()  # 再生キューをクリアして、play_next が暴走しないようにする
        await ctx.send("Disconnected.")
    else:
        await ctx.send("I'm not connected.")

@bot.command()
async def volume(ctx, level: int):
    global current_volume
    if 0 <= level <= 100:
        current_volume = level / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = current_volume
        await ctx.send(f"Volume set to {level}%.")
    else:
        await ctx.send("Volume must be between 0 and 100.")

@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user and after.channel is None:
        queue.clear()
    elif member != bot.user:
        voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
        if voice_client and voice_client.channel and len(voice_client.channel.members) == 1:
            await asyncio.sleep(10)
            if len(voice_client.channel.members) == 1:
                await voice_client.disconnect()

bot.run("TOKEN") # トークンを適切に差し替えてください

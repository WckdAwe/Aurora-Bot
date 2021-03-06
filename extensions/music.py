import asyncio
import discord
from configs import lang
from discord.ext import commands

if not discord.opus.is_loaded():
    discord.opus.load_opus('opus')


class VoiceEntry:
    def __init__(self, message, player):
        self.requester = message.author
        self.channel = message.channel
        self.player = player

    def __str__(self):
        fmt = lang['MUSIC']['VOICE_ENTRY']
        duration = self.player.duration
        if duration:
            fmt = fmt + ' [length: {0[0]}m {0[1]}s]'.format(divmod(duration, 60))

        return fmt.format(player=self.player, requester=self.requester)


class VoiceState:
    def __init__(self, bot):
        self.current = None
        self.voice = None
        self.bot = bot
        self.play_next_song = asyncio.Event()
        self.songs = asyncio.Queue()
        self.skip_votes = set()  # a set of user_ids that voted
        self.audio_player = self.bot.loop.create_task(self.audio_player_task())

    def is_playing(self):
        if self.voice is None or self.current is None:
            return False

        player = self.current.player
        return not player.is_done()

    @property
    def player(self):
        return self.current.player

    def skip(self):
        self.skip_votes.clear()
        if self.is_playing():
            self.player.stop()

    def toggle_next(self):
        self.bot.loop.call_soon_threadsafe(self.play_next_song.set)

    async def audio_player_task(self):
        while True:
            self.play_next_song.clear()
            self.current = await self.songs.get()
            await self.bot.send_message(self.current.channel,
                                        str(lang['MUSIC']['NOW_PLAYING']).format(current_song=self.current))
            self.current.player.start()
            await self.play_next_song.wait()


class Music:
    """Let Aurora become your DJ."""
    def __init__(self, bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, server):
        state = self.voice_states.get(server.id)
        if state is None:
            state = VoiceState(self.bot)
            self.voice_states[server.id] = state

        return state

    async def create_voice_client(self, channel):
        voice = await self.bot.join_voice_channel(channel)
        state = self.get_voice_state(channel.server)
        state.voice = voice

    def __unload(self):
        for state in self.voice_states.values():
            try:
                state.audio_player.cancel()
                if state.voice:
                    self.bot.loop.create_task(state.voice.disconnect())
            except:
                pass

    @commands.group(pass_context=True)
    async def music(self, ctx):
        """Music related commands"""
        if ctx.invoked_subcommand is None:
            await self.bot.say('Seems like commands "{0.subcommand_passed}" doesn\'t exist!'.format(ctx))

    @music.command(pass_context=True, no_pm=True)
    async def join(self, ctx, *, channel: discord.Channel = None):
        """Joins a specific voice channel."""
        if channel is None:  # If no channel is provided, try to join the users channel.
            channel = ctx.message.author.voice_channel

        try:
            await self.create_voice_client(channel)
        except discord.ClientException:
            await self.bot.say(lang['MUSIC']['IN_ANOTHER_VOICE_CHANNEL'])
        except discord.InvalidArgument:
            await self.bot.say(lang['MUSIC']['NOT_A_VOICE_CHANNEL'])
        else:
            await self.bot.say(str(lang['MUSIC']['READY_TO_PLAY']).format(channel=channel))

    @music.command(pass_context=True, no_pm=True)
    async def summon(self, ctx):
        """Summons the bot on your voice channel."""
        summoned_channel = ctx.message.author.voice_channel
        if summoned_channel is None:
            await self.bot.say(lang['MUSIC']['USER_NOT_IN_VOICE_CHANNEL'])
            return False

        state = self.get_voice_state(ctx.message.server)
        if state.voice is None:
            state.voice = await self.bot.join_voice_channel(summoned_channel)
        else:
            await state.voice.move_to(summoned_channel)

        return True

    @music.command(pass_context=True, no_pm=True)
    async def play(self, ctx, *, song: str):
        """Plays a song.
        If there is a song currently in the queue, then it is
        queued until the next song is done playing.
        This command automatically searches as well from YouTube.
        The list of supported sites can be found here:
        https://rg3.github.io/youtube-dl/supportedsites.html
        """
        state = self.get_voice_state(ctx.message.server)
        opts = {
            'default_search': 'auto',
            'quiet': True,
        }

        if state.voice is None:
            success = await ctx.invoke(self.summon)
            if not success:
                return

        try:
            player = await state.voice.create_ytdl_player(song, ytdl_options=opts, after=state.toggle_next)
        except Exception as e:
            fmt = 'An error occurred while processing this request: ```py\n{}: {}\n```'
            await self.bot.send_message(ctx.message.channel, fmt.format(type(e).__name__, e))
        else:
            player.volume = 0.6
            entry = VoiceEntry(ctx.message, player)
            await self.bot.say(str(lang['MUSIC']['ENQUEUE_SUCCESS']).format(entry=entry))
            await state.songs.put(entry)

    @music.command(pass_context=True, no_pm=True)
    async def volume(self, ctx, value: int):
        """Sets the volume of the currently playing song."""

        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.volume = value / 100
            await self.bot.say(str(lang['MUSIC']['VOLUME_CHANGE']).format(volume=player.volume))

    @music.command(pass_context=True, no_pm=True)
    async def pause(self, ctx):
        """Pauses the currently played song."""
        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.pause()

    @music.command(pass_context=True, no_pm=True)
    async def resume(self, ctx):
        """Resumes the currently played song."""
        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.resume()

    @music.command(pass_context=True, no_pm=True)
    async def stop(self, ctx):
        """Stops playing audio and leaves the voice channel.
        This also clears the queue.
        """
        server = ctx.message.server
        state = self.get_voice_state(server)

        if state.is_playing():
            player = state.player
            player.stop()

        try:
            state.audio_player.cancel()
            del self.voice_states[server.id]
            await state.voice.disconnect()
        except:
            pass

    @music.command(pass_context=True, no_pm=True)
    async def skip(self, ctx):
        """Vote to skip a song. The song requester can automatically skip.
        3 skip votes are needed for the song to be skipped.
        """

        state = self.get_voice_state(ctx.message.server)
        if not state.is_playing():
            await self.bot.say(str(lang['MUSIC']['NOT_PLAYING']))
            return

        voter = ctx.message.author
        if voter == state.current.requester:
            await self.bot.say(str(lang['MUSIC']['SKIP_REQUESTER']).format(current_song=state.current,
                                                                           requester=state.current.requester))
            state.skip()
        elif voter.id not in state.skip_votes:
            state.skip_votes.add(voter.id)
            total_votes = len(state.skip_votes)
            if total_votes >= 3:
                await self.bot.say(lang['MUSIC']['SKIP_VOTE_PASSED'])
                state.skip()
            else:
                await self.bot.say(str(lang['MUSIC']['SKIP_VOTE_PASSED']).format(current_votes=total_votes))
        else:
            await self.bot.say(lang['MUSIC']['SKIP_ALREADY_VOTED'])

    @music.command(pass_context=True, no_pm=True)
    async def playing(self, ctx):
        """Shows info about the currently played song."""

        state = self.get_voice_state(ctx.message.server)
        if state.current is None:
            await self.bot.say(lang['MUSIC']['NOT_PLAYING'])
        else:
            # skip_count = len(state.skip_votes)
            await self.bot.say(str(lang['MUSIC']['NOW_PLAYING']).format(current_song=state.current))


def setup(bot):
    bot.add_cog(Music(bot))

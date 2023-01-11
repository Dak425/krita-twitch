#  Copyright (c) 2021. Lena "Teekeks" During <info@teawork.de>
"""
EventSub Client
---------------

EventSub lets you listen for events that happen on Twitch.

The EventSub client runs in its own thread, calling the given callback function whenever an event happens.

Look at the `Twitch EventSub reference <https://dev.twitch.tv/docs/eventsub/eventsub-reference>`__ to find the topics
you are interested in.

************
Requirements
************

.. note:: Please note that Your Endpoint URL has to be HTTPS, has to run on Port 443 and requires a valid, non self signed certificate
            This most likely means, that you need a reverse proxy like nginx. You can also hand in a valid ssl context to be used in the constructor.

In the case that you don't hand in a valid ssl context to the constructor, you can specify any port you want in the constructor and handle the
bridge between this program and your public URL on port 443 via reverse proxy.\n
You can check on whether or not your webhook is publicly reachable by navigating to the URL set in `callback_url`.
You should get a 200 response with the text `pyTwitchAPI eventsub`.

*******************
Listening to topics
*******************

After you started your EventSub client, you can use the :code:`listen_` prefixed functions to listen to the topics you are interested in.

The function you hand in as callback will be called whenever that event happens with the event data as a parameter.

************
Code Example
************

.. code-block:: python

    from kritatwitch.vendor.twitchAPI.twitch import Twitch
    from kritatwitch.vendor.twitchAPI.helper import first
    from kritatwitch.vendor.twitchAPI.eventsub import EventSub
    import asyncio


    TARGET_USERNAME = 'target_username_here'
    EVENTSUB_URL = 'https://url.to.your.webhook.com'
    APP_ID = 'your_app_id'
    APP_SECRET = 'your_app_secret'


    async def on_follow(data: dict):
        # our event happend, lets do things with the data we got!
        print(data)


    async def eventsub_example():
        # create the api instance and get the ID of the target user
        twitch = await Twitch(APP_ID, APP_SECRET)
        user = await first(twitch.get_users(logins=TARGET_USERNAME))

        # basic setup, will run on port 8080 and a reverse proxy takes care of the https and certificate
        event_sub = EventSub(EVENTSUB_URL, APP_ID, 8080, twitch)

        # unsubscribe from all old events that might still be there
        # this will ensure we have a clean slate
        await event_sub.unsubscribe_all()
        # start the eventsub client
        event_sub.start()
        # subscribing to the desired eventsub hook for our user
        # the given function will be called every time this event is triggered
        await event_sub.listen_channel_follow(user.id, on_follow)

        # eventsub will run in its own process
        # so lets just wait for user input before shutting it all down again
        try:
            input('press Enter to shut down...')
        finally:
            # stopping both eventsub as well as gracefully closing the connection to the API
            await event_sub.stop()
            await twitch.close()
        print('done')


    # lets run our example
    asyncio.run(eventsub_example())

*******************
Class Documentation
*******************
"""
import datetime
import random
import string
import time

from kritatwitch.vendor import aiohttp

from .helper import TWITCH_API_BASE_URL, remove_none_values
from .types import *
from kritatwitch.vendor.aiohttp import web, ClientSession
import threading
import asyncio
from logging import getLogger, Logger
from .twitch import Twitch
from concurrent.futures._base import CancelledError
from ssl import SSLContext
from .types import EventSubSubscriptionTimeout, EventSubSubscriptionConflict, EventSubSubscriptionError
import hmac
import hashlib

from typing import Union, Callable, Optional, Awaitable


__all__ = ['CALLBACK_TYPE', 'EventSub']


CALLBACK_TYPE = Callable[[dict], Awaitable[None]]


class EventSub:
    """EventSub integration for the Twitch Helix API.
    """

    def __init__(self,
                 callback_url: str,
                 api_client_id: str,
                 port: int,
                 twitch: Twitch,
                 ssl_context: Optional[SSLContext] = None):
        """
        :param callback_url: The full URL of the webhook.
        :param api_client_id: The id of your API client
        :param port: the port on which this webhook should run
        :param twitch: a app authenticated instance of :const:`~twitchAPI.twitch.Twitch`
        :param ssl_context: optional ssl context to be used |default| :code:`None`
        """
        self.callback_url: str = callback_url
        """The full URL of the webhook."""
        self.__client_id: str = api_client_id
        self._port: int = port
        self.__ssl_context: Optional[SSLContext] = ssl_context
        self.__twitch: Twitch = twitch
        self.logger: Logger = getLogger('twitchAPI.eventsub')
        """The logger used for EventSub related log messages"""
        self.secret: str = ''.join(random.choice(string.ascii_lowercase) for i in range(20))
        """A random secret string. Set this for added security. |default| :code:`A random 20 character long string`"""
        self.wait_for_subscription_confirm: bool = True
        """Set this to false if you don't want to wait for a subscription confirm. |default| :code:`True`"""
        self.wait_for_subscription_confirm_timeout: int = 30
        """Max time in seconds to wait for a subscription confirmation. Only used if ``wait_for_subscription_confirm`` is set to True. 
            |default| :code:`30`"""
        self.unsubscribe_on_stop: bool = True
        """Unsubscribe all currently active Webhooks on calling :const:`~twitchAPI.eventsub.EventSub.stop()` |default| :code:`True`"""
        self._host: str = '0.0.0.0'
        self.__running = False
        self.__callbacks = {}
        self.__active_webhooks = {}
        self.__hook_thread: Union['threading.Thread', None] = None
        self.__hook_loop: Union['asyncio.AbstractEventLoop', None] = None
        self.__hook_runner: Union['web.AppRunner', None] = None
        if not self.callback_url.startswith('https'):
            raise RuntimeError('HTTPS is required for authenticated webhook.\n'
                               + 'Either use non authenticated webhook or use a HTTPS proxy!')

    def __build_runner(self):
        hook_app = web.Application()
        hook_app.add_routes([web.post('/callback', self.__handle_callback),
                             web.get('/', self.__handle_default)])
        return web.AppRunner(hook_app)

    def __run_hook(self, runner: 'web.AppRunner'):
        self.__hook_runner = runner
        self.__hook_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.__hook_loop)
        self.__hook_loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, str(self._host), self._port, ssl_context=self.__ssl_context)
        self.__hook_loop.run_until_complete(site.start())
        self.logger.info('started twitch API event sub on port ' + str(self._port))
        try:
            self.__hook_loop.run_forever()
        except (CancelledError, asyncio.CancelledError):
            self.logger.debug('we got cancelled')

    def start(self):
        """Starts the EventSub client

        :rtype: None
        :raises RuntimeError: if EventSub is already running
        """
        if self.__running:
            raise RuntimeError('already started')
        self.__hook_thread = threading.Thread(target=self.__run_hook, args=(self.__build_runner(),))
        self.__running = True
        self.__hook_thread.start()

    async def stop(self):
        """Stops the EventSub client

        This also unsubscribes from all known subscriptions if unsubscribe_on_stop is True

        :rtype: None
        """
        if self.__hook_runner is not None and self.unsubscribe_on_stop:
            await self.unsubscribe_all_known()
        tasks = {t for t in asyncio.all_tasks(loop=self.__hook_loop) if not t.done()}
        for task in tasks:
            task.cancel()
        # ensure all client sessions are closed
        await asyncio.sleep(0.25)
        self.__hook_loop.call_soon_threadsafe(self.__hook_loop.stop)
        self.__hook_runner = None
        self.__running = False

    # ==================================================================================================================
    # HELPER
    # ==================================================================================================================

    def __build_request_header(self):
        token = self.__twitch.get_app_token()
        if token is None:
            raise TwitchAuthorizationException('no Authorization set!')
        return {
            'Client-ID': self.__client_id,
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

    # FIXME requires port
    async def __api_post_request(self, url: str, data: Union[dict, None] = None):
        headers = self.__build_request_header()
        async with ClientSession() as session:
            return await session.post(url, headers=headers, json=data)

    async def __api_get_request(self, url: str):
        headers = self.__build_request_header()
        async with ClientSession() as session:
            return await session.get(url, headers=headers)

    async def __api_delete_request(self, url: str):
        headers = self.__build_request_header()
        async with ClientSession() as session:
            return await session.delete(url, headers=headers)

    def __add_callback(self, c_id: str, callback):
        self.__callbacks[c_id] = {'id': c_id, 'callback': callback, 'active': False}

    def __activate_callback(self, c_id: str):
        self.__callbacks[c_id]['active'] = True

    async def _subscribe(self, sub_type: str, sub_version: str, condition: dict, callback) -> str:
        """"Subscribe to Twitch Topic"""
        self.logger.debug(f'subscribe to {sub_type} version {sub_version} with condition {condition}')
        data = {
            'type': sub_type,
            'version': sub_version,
            'condition': condition,
            'transport': {
                'method': 'webhook',
                'callback': f'{self.callback_url}/callback',
                'secret': self.secret
            }
        }
        r_data = await self.__api_post_request(TWITCH_API_BASE_URL + 'eventsub/subscriptions', data=data)
        result = await r_data.json()
        error = result.get('error')
        if r_data.status == 500:
            raise TwitchBackendException(error)
        if error is not None:
            if error.lower() == 'conflict':
                raise EventSubSubscriptionConflict(result.get('message', ''))
            raise EventSubSubscriptionError(result.get('message'))
        sub_id = result['data'][0]['id']
        self.__add_callback(sub_id, callback)
        if self.wait_for_subscription_confirm:
            timeout = datetime.datetime.utcnow() + datetime.timedelta(
                seconds=self.wait_for_subscription_confirm_timeout)
            while timeout >= datetime.datetime.utcnow():
                if self.__callbacks[sub_id]['active']:
                    return sub_id
                time.sleep(0.01)
            self.__callbacks.pop(sub_id, None)
            raise EventSubSubscriptionTimeout()
        return sub_id

    async def _verify_signature(self, request: 'web.Request') -> bool:
        expected = request.headers['Twitch-Eventsub-Message-Signature']
        hmac_message = request.headers['Twitch-Eventsub-Message-Id'] + \
            request.headers['Twitch-Eventsub-Message-Timestamp'] + await request.text()
        sig = 'sha256=' + hmac.new(bytes(self.secret, 'utf-8'),
                                   msg=bytes(hmac_message, 'utf-8'),
                                   digestmod=hashlib.sha256).hexdigest().lower()
        return sig == expected

    # ==================================================================================================================
    # HANDLERS
    # ==================================================================================================================

    async def __handle_default(self, request: 'web.Request'):
        return web.Response(text="pyTwitchAPI EventSub")

    async def __handle_challenge(self, request: 'web.Request', data: dict):
        self.logger.debug(f'received challenge for subscription {data.get("subscription").get("id")}')
        if not await self._verify_signature(request):
            self.logger.warning(f'message signature is not matching! Discarding message')
            return web.Response(status=403)
        self.__activate_callback(data.get('subscription').get('id'))
        return web.Response(text=data.get('challenge'))

    async def __handle_callback(self, request: 'web.Request'):
        data: dict = await request.json()
        if data.get('challenge') is not None:
            return await self.__handle_challenge(request, data)
        sub_id = data.get('subscription', {}).get('id')
        callback = self.__callbacks.get(sub_id)
        if callback is None:
            self.logger.error(f'received event for unknown subscription with ID {sub_id}')
        else:
            if not await self._verify_signature(request):
                self.logger.warning(f'message signature is not matching! Discarding message')
                return web.Response(status=403)
            self.__hook_loop.create_task(callback['callback'](data))
        return web.Response(status=200)

    async def unsubscribe_all(self):
        """Unsubscribe from all subscriptions"""
        ret = await self.__twitch.get_eventsub_subscriptions()
        async for d in ret:
            try:
                await self.__twitch.delete_eventsub_subscription(d.id)
            except TwitchAPIException as e:
                self.logger.warning(f'failed to unsubscribe from event {d.id}: {str(e)}')
        self.__callbacks.clear()

    async def unsubscribe_all_known(self):
        """Unsubscribe from all subscriptions known to this client."""
        for key, value in self.__callbacks.items():
            self.logger.debug(f'unsubscribe from event {key}')
            try:
                await self.__twitch.delete_eventsub_subscription(key)
            except TwitchAPIException as e:
                self.logger.warning(f'failed to unsubscribe from event {key}: {str(e)}')
        self.__callbacks.clear()

    async def unsubscribe_topic(self, topic_id: str) -> bool:
        """Unsubscribe from a specific topic."""
        try:
            await self.__twitch.delete_eventsub_subscription(topic_id)
            self.__callbacks.pop(topic_id, None)
            return True
        except TwitchAPIException as e:
            self.logger.warning(f'failed to unsubscribe from {topic_id}: {str(e)}')
        return False

    async def listen_channel_update(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A broadcaster updates their channel properties e.g., category, title, mature flag, broadcast, or language.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelupdate

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.update', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_follow(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A specified channel receives a follow.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelfollow

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.follow', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_subscribe(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A notification when a specified channel receives a subscriber. This does not include resubscribes.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelsubscribe

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.subscribe', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_subscription_end(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A notification when a subscription to the specified channel ends.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelsubscriptionend

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.subscription.end', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_subscription_gift(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A notification when a viewer gives a gift subscription to one or more users in the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelsubscriptiongift

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.subscription.gift', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_subscription_message(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A notification when a user sends a resubscription chat message in a specific channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelsubscriptionmessage

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.subscription.message',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_cheer(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A user cheers on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelcheer

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.cheer',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_raid(self,
                                  callback: CALLBACK_TYPE,
                                  to_broadcaster_user_id: Optional[str] = None,
                                  from_broadcaster_user_id: Optional[str] = None) -> str:
        """A broadcaster raids another broadcaster’s channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelraid

        :param str from_broadcaster_user_id: The broadcaster user ID that created the channel raid you want to get notifications for.
        :param str to_broadcaster_user_id: The broadcaster user ID that received the channel raid you want to get notifications for.
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.raid',
                                     '1',
                                     remove_none_values({
                                         'from_broadcaster_user_id': from_broadcaster_user_id,
                                         'to_broadcaster_user_id': to_broadcaster_user_id}),
                                     callback)

    async def listen_channel_ban(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A viewer is banned from the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelban

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.ban',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_unban(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A viewer is unbanned from the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelunban

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.unban',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_moderator_add(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """Moderator privileges were added to a user on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelmoderatoradd

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.moderator.add',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_moderator_remove(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """Moderator privileges were removed from a user on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelmoderatorremove

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.moderator.remove',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_points_custom_reward_add(self, broadcaster_user_id: str,
                                                      callback: CALLBACK_TYPE) -> str:
        """A custom channel points reward has been created for the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelchannel_points_custom_rewardadd

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.channel_points_custom_reward.add',
                                     '1',
                                     {'broadcaster_user_id': broadcaster_user_id},
                                     callback)

    async def listen_channel_points_custom_reward_update(self,
                                                         broadcaster_user_id: str,
                                                         callback: CALLBACK_TYPE,
                                                         reward_id: Optional[str] = None) -> str:
        """A custom channel points reward has been updated for the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelchannel_points_custom_rewardupdate

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param str reward_id: the id of the reward you want to get updates from. |default| :code:`None`
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.channel_points_custom_reward.update',
                                     '1',
                                     remove_none_values({
                                         'broadcaster_user_id': broadcaster_user_id,
                                         'reward_id': reward_id}),
                                     callback)

    async def listen_channel_points_custom_reward_remove(self,
                                                         broadcaster_user_id: str,
                                                         callback: CALLBACK_TYPE,
                                                         reward_id: Optional[str] = None) -> str:
        """A custom channel points reward has been removed from the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelchannel_points_custom_rewardremove

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param str reward_id: the id of the reward you want to get updates from. |default| :code:`None`
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.channel_points_custom_reward.remove',
                                     '1',
                                     remove_none_values({
                                         'broadcaster_user_id': broadcaster_user_id,
                                         'reward_id': reward_id}),
                                     callback)

    async def listen_channel_points_custom_reward_redemption_add(self,
                                                                 broadcaster_user_id: str,
                                                                 callback: CALLBACK_TYPE,
                                                                 reward_id: Optional[str] = None) -> str:
        """A viewer has redeemed a custom channel points reward on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelchannel_points_custom_reward_redemptionadd

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param str reward_id: the id of the reward you want to get updates from. |default| :code:`None`
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.channel_points_custom_reward_redemption.add',
                                     '1',
                                     remove_none_values({
                                         'broadcaster_user_id': broadcaster_user_id,
                                         'reward_id': reward_id}),
                                     callback)

    async def listen_channel_points_custom_reward_redemption_update(self,
                                                                    broadcaster_user_id: str,
                                                                    callback: CALLBACK_TYPE,
                                                                    reward_id: Optional[str] = None) -> str:
        """A redemption of a channel points custom reward has been updated for the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelchannel_points_custom_reward_redemptionupdate

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param str reward_id: the id of the reward you want to get updates from. |default| :code:`None`
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.channel_points_custom_reward_redemption.update',
                                     '1',
                                     remove_none_values({
                                         'broadcaster_user_id': broadcaster_user_id,
                                         'reward_id': reward_id}),
                                     callback)

    async def listen_channel_poll_begin(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A poll started on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpollbegin

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.poll.begin', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_poll_progress(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """Users respond to a poll on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpollprogress

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.poll.progress', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_poll_end(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A poll ended on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpollend

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.poll.end', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_prediction_begin(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Prediction started on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpredictionbegin

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.prediction.begin', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_prediction_progress(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """Users participated in a Prediction on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpredictionprogress

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.prediction.progress', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_prediction_lock(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Prediction was locked on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpredictionlock

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.prediction.lock', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_channel_prediction_end(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Prediction ended on a specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelpredictionend

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.prediction.end', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_drop_entitlement_grant(self,
                                            organisation_id: str,
                                            callback: CALLBACK_TYPE,
                                            category_id: Optional[str] = None,
                                            campaign_id: Optional[str] = None) -> str:
        """An entitlement for a Drop is granted to a user.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#dropentitlementgrant

        :param str organisation_id: The organization ID of the organization that owns the game on the developer portal.
        :param str category_id: The category (or game) ID of the game for which entitlement notifications will be received.
                |default| :code:`None`
        :param str campaign_id: The campaign ID for a specific campaign for which entitlement notifications will be received.
                |default| :code:`None`
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('drop.entitlement.grant',
                                     '1',
                                     remove_none_values({
                                         'organization_id': organisation_id,
                                         'category_id': category_id,
                                         'campaign_id': campaign_id
                                     }),
                                     callback)

    async def listen_extension_bits_transaction_create(self,
                                                       extension_client_id: str,
                                                       callback: CALLBACK_TYPE) -> str:
        """A Bits transaction occurred for a specified Twitch Extension.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#extensionbits_transactioncreate

        :param str extension_client_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('extension.bits_transaction.create', '1', {'extension_client_id': extension_client_id}, callback)

    async def listen_goal_begin(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A goal begins on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelgoalbegin

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.goal.begin', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_goal_progress(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A goal makes progress on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelgoalprogress

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.goal.progress', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_goal_end(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A goal ends on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelgoalend

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.goal.end', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_hype_train_begin(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Hype Train begins on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelhype_trainbegin

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.hype_train.begin', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_hype_train_progress(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Hype Train makes progress on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelhype_trainprogress

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.hype_train.progress', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_hype_train_end(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """A Hype Train ends on the specified channel.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#channelhype_trainend

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('channel.hype_train.end', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_stream_online(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """The specified broadcaster starts a stream.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#streamonline

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('stream.online', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_stream_offline(self, broadcaster_user_id: str, callback: CALLBACK_TYPE) -> str:
        """The specified broadcaster stops a stream.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#streamoffline

        :param str broadcaster_user_id: the id of the user you want to listen to
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('stream.offline', '1', {'broadcaster_user_id': broadcaster_user_id}, callback)

    async def listen_user_authorization_grant(self, client_id: str, callback: CALLBACK_TYPE) -> str:
        """A user’s authorization has been granted to your client id.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#userauthorizationgrant

        :param str client_id: Your application’s client id.
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('user.authorization.grant', '1', {'client_id': client_id}, callback)

    async def listen_user_authorization_revoke(self, client_id: str, callback: CALLBACK_TYPE) -> str:
        """A user’s authorization has been revoked for your client id.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#userauthorizationrevoke

        :param str client_id: Your application’s client id.
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('user.authorization.revoke', '1', {'client_id': client_id}, callback)

    async def listen_user_update(self, user_id: str, callback: CALLBACK_TYPE) -> str:
        """A user has updated their account.

        For more information see here: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types#userupdate

        :param str user_id: The user ID for the user you want update notifications for.
        :param Callable[[dict],Awaitable[None]] callback: function for callback
        :raises ~twitchAPI.types.EventSubSubscriptionConflict: if a conflict was found with this subscription
            (e.g. already subscribed to this exact topic)
        :raises ~twitchAPI.types.EventSubSubscriptionTimeout: if :code:`wait_for_subscription_confirm`
            is true and the subscription was not fully confirmed in time
        :raises ~twitchAPI.types.EventSubSubscriptionError: if the subscription failed (see error message for details)
        :raises ~twitchAPI.types.TwitchBackendException: if the subscription failed due to a twitch backend error
        :rtype: str
        """
        return await self._subscribe('user.update', '1', {'user_id': user_id}, callback)

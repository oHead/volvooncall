#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Communicate with VOC server."""

import logging
from datetime import timedelta
from functools import partial
from sys import argv, version_info
from os import environ as env
from os.path import join, dirname, expanduser
from itertools import product
from json import dumps as to_json
from collections import OrderedDict
import asyncio
import aiohttp
import contextlib

from requests import Session, RequestException
from requests.compat import urljoin

from util import obj_parser, json_serialize, is_valid_path, find_path, json_loads
from util import owntracks_encrypt  # noqa: F401

_ = version_info >= (3, 7) or exit('Python 3.7 required')

__version__ = '0.6.13'

_LOGGER = logging.getLogger(__name__)

SERVICE_URL = 'https://vocapi{region}.wirelesscar.net/customerapi/rest/v3.0/'
DEFAULT_SERVICE_URL = SERVICE_URL.format(region='')

HEADERS = {'X-Device-Id': 'Device',
           'X-OS-Type': 'Android',
           'X-Originator-Type': 'App',
           'X-OS-Version': '22',
           'Content-Type': 'application/json'}

TIMEOUT = timedelta(seconds=30)


_LOGGER.debug('Loaded %s version: %s', __name__, __version__)


class Connection(contextlib.AbstractAsyncContextManager):

    """Connection to the VOC server."""

    def __init__(self, username, password,
                 service_url=None, region=None, **_):
        """Initialize."""
        _LOGGER.info('Initializing %s version: %s', __name__, __version__)

        self._session = aiohttp.ClientSession(
            headers=HEADERS,
            raise_for_status=True,
            auth=aiohttp.BasicAuth(username,
                                   password),
            timeout=aiohttp.ClientTimeout(total=TIMEOUT.seconds))
        self._service_url = SERVICE_URL.format(region='-'+region) \
            if region else service_url or DEFAULT_SERVICE_URL
        self._state = {}
        _LOGGER.debug('Using service <%s>', self._service_url)
        _LOGGER.debug('User: <%s>', username)

    async def __aexit__(self, exc_type, exc, tb):
        await self._session.close()

    async def _request(self, method, ref, rel=None):
        """Perform a query to the online service."""
        try:
            url = urljoin(rel or self._service_url, ref)
            _LOGGER.debug('Request for %s', url)
            async with method(url) as response:
                res = await response.json(loads=json_loads)
                _LOGGER.debug('Received %s', res)
                return res
        except RequestException as error:
            _LOGGER.warning('Failure when communcating with the server: %s',
                            error)
            raise

    async def get(self, ref, rel=None):
        """Perform a query to the online service."""
        return await self._request(self._session.get, ref, rel)

    async def post(self, ref, rel=None, **data):
        """Perform a query to the online service."""
        return await self._request(partial(self._session.post, json=data), ref, rel)

    async def update(self, journal=False, reset=False):
        """Update status."""
        try:
            _LOGGER.info('Updating')
            if not self._state or reset:
                _LOGGER.info('Querying vehicles')
                user = await self.get('customeraccounts')
                _LOGGER.debug('Account for <%s> received',
                              user['username'])
                self._state = {}
                for vehicle in user['accountVehicleRelations']:
                    rel = await self.get(vehicle)
                    url = rel['vehicle'] + '/'
                    state = await self.get('attributes', url)
                    self._state.update({url: state})
            for vehicle in self.vehicles:
                await vehicle.update(journal=journal)
            _LOGGER.debug('State: %s', self._state)
            return True
        except (IOError, OSError) as error:
            _LOGGER.warning('Could not query server: %s', error)

    async def update_vehicle(self, vehicle, journal=False):
        url = vehicle._url
        self._state[url].update(
            await self.get('status', url))
        self._state[url].update(
            await self.get('position', url))
        if journal:
            self._state[url].update(
                await self.get('trips', url))

    @property
    def vehicles(self):
        """Return vehicle state."""
        return (Vehicle(self, url)
                for url in self._state)

    def vehicle(self, vin):
        """Return vehicle for given vin."""
        return next((vehicle for vehicle in self.vehicles
                     if vehicle.unique_id == vin.lower()), None)

    def vehicle_attrs(self, vehicle_url):
        return self._state.get(vehicle_url)


class Vehicle(object):
    """Convenience wrapper around the state returned from the server."""

    def __init__(self, conn, url):
        self._connection = conn
        self._url = url

    async def update(self, journal=False):
        await self._connection.update_vehicle(self, journal)

    @property
    def attrs(self):
        return self._connection.vehicle_attrs(self._url)

    def has_attr(self, attr):
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr):
        return find_path(self.attrs, attr)

    @property
    def unique_id(self):
        return (self.registration_number or
                self.vin).lower()

    @property
    def position(self):
        return self.attrs.get('position')

    @property
    def registration_number(self):
        return self.attrs.get('registrationNumber')

    @property
    def vin(self):
        return self.attrs.get('vin')

    @property
    def model_year(self):
        return self.attrs.get('modelYear')

    @property
    def vehicle_type(self):
        return self.attrs.get('vehicleType')

    @property
    def odometer(self):
        return self.attrs.get('odometer')

    @property
    def fuel_amount_level(self):
        return self.attrs.get('fuelAmountLevel')

    @property
    def distance_to_empty(self):
        return self.attrs.get('distanceToEmpty')

    @property
    def is_honk_and_blink_supported(self):
        return self.attrs.get('honkAndBlinkSupported')

    @property
    def doors(self):
        return self.attrs.get('doors')

    @property
    def windows(self):
        return self.attrs.get('windows')

    @property
    def is_lock_supported(self):
        return self.attrs.get('lockSupported')

    @property
    def is_unlock_supported(self):
        return self.attrs.get('unlockSupported')

    @property
    def is_locked(self):
        return self.attrs.get('carLocked')

    @property
    def heater(self):
        return self.attrs.get('heater')

    @property
    def is_remote_heater_supported(self):
        return self.attrs.get('remoteHeaterSupported')

    @property
    def is_preclimatization_supported(self):
        return self.attrs.get('preclimatizationSupported')

    @property
    def is_journal_supported(self):
        return (self.attrs.get('journalLogSupported') and
                self.attrs.get('journalLogEnabled'))

    @property
    def is_engine_running(self):
        engine_remote_start_status = self.attrs.get('ERS',
                                                    {}).get('status', '')
        return (self.attrs.get('engineRunning') or
                'on' in engine_remote_start_status)

    @property
    def is_engine_start_supported(self):
        return (self.attrs.get('engineStartSupported') and
                self.attrs.get('ERS'))

    async def get(self, query):
        """Perform a query to the online service."""
        return await self._connection.get(query, self._url)

    async def post(self, query, **data):
        """Perform a query to the online service."""
        return await self._connection.post(query, self._url, **data)

    async def call(self, method, **data):
        """Make remote method call."""
        try:
            res = await self.post(method, **data)

            if 'service' and 'status' not in res:
                _LOGGER.warning('Failed to execute: %s', res['status'])
                return

            if res['status'] not in ['Queued', 'Started']:
                _LOGGER.warning('Failed to execute: %s', res['status'])
                return

            # if Queued -> wait?

            service_url = res['service']
            res = await self.get(service_url)

            if 'service' and 'status' not in res:
                _LOGGER.warning('Message not delivered: %s', res['status'])

            # if still Queued -> wait?

            if res['status'] not in ['MessageDelivered',
                                     'Successful',
                                     'Started']:
                _LOGGER.warning('Message not delivered: %s', res['status'])
                return

            _LOGGER.debug('Message delivered')
            return True
        except RequestException as error:
            _LOGGER.warning('Failure to execute: %s', error)

    @staticmethod
    def any_open(doors):
        """
        >>> Vehicle.any_open({'frontLeftWindowOpen': False,
        ...                   'frontRightWindowOpen': False,
        ...                   'timestamp': 'foo'})
        False

        >>> Vehicle.any_open({'frontLeftWindowOpen': True,
        ...                   'frontRightWindowOpen': False,
        ...                   'timestamp': 'foo'})
        True
        """
        return doors and any(doors[door]
                             for door in doors
                             if 'Open' in door)

    @property
    def any_window_open(self):
        return self.any_open(self.windows)

    @property
    def any_door_open(self):
        return self.any_open(self.doors)

    @property
    def position_supported(self):
        """Return true if vehicle has position."""
        return 'position' in self.attrs

    @property
    def heater_supported(self):
        """Return true if vehicle has heater."""
        return ((self.is_remote_heater_supported or
                 self.is_preclimatization_supported) and
                'heater' in self.attrs)

    @property
    def is_heater_on(self):
        """Return status of heater."""
        return (self.heater_supported and
                'status' in self.heater and
                self.heater['status'] != 'off')

    @property
    def trips(self):
        """Return trips."""
        return self.attrs.get('trips')

    def honk_and_blink(self):
        """Honk and blink."""
        if self.is_honk_and_blink_supported:
            self.call('honkAndBlink')

    async def lock(self):
        """Lock."""
        if self.is_lock_supported:
            await self.call('lock')
            await self.update()
        else:
            _LOGGER.warning('Lock not supported')

    async def unlock(self):
        """Unlock."""
        if self.is_unlock_supported:
            await self.call('unlock')
            await self.update()
        else:
            _LOGGER.warning('Unlock not supported')

    async def start_engine(self):
        if self.is_engine_start_supported:
            await self.call('engine/start', runtime=5)
            await self.update()
        else:
            _LOGGER.warning('Engine start not supported.')

    async def stop_engine(self):
        if self.is_engine_start_supported:
            await self.call('engine/stop')
            await self.update()
        else:
            _LOGGER.warning('Engine stop not supported.')

    async def start_heater(self):
        """Turn on/off heater."""
        if self.is_remote_heater_supported:
            await self.call('heater/start')
            await self.update()
        elif self.is_preclimatization_supported:
            await self.call('preclimatization/start')
            await self.update()
        else:
            _LOGGER.warning('No heater or preclimatization support.')

    async def stop_heater(self):
        """Turn on/off heater."""
        if self.is_remote_heater_supported:
            await self.call('heater/stop')
            await self.update()
        elif self.is_preclimatization_supported:
            await self.call('preclimatization/stop')
            await self.update()
        else:
            _LOGGER.warning('No heater or preclimatization support.')

    def __str__(self):
        return '%s (%s/%d) %s' % (
            self.registration_number,
            self.vehicle_type,
            self.model_year,
            self.vin)

    @property
    def dashboard(self):
        from dashboard import Dashboard
        return Dashboard(self)

    @property
    def json(self):
        """Return JSON representation."""
        return to_json(
            OrderedDict(sorted(self.attrs.items())),
            indent=4, default=json_serialize)


def read_credentials():
    """Read credentials from file."""
    for directory, filename in product(
            [dirname(argv[0]),
             expanduser('~'),
             env.get('XDG_CONFIG_HOME',
                     join(expanduser('~'), '.config'))],
            ['voc.conf',
             '.voc.conf']):
        try:
            config = join(directory, filename)
            _LOGGER.debug('checking for config file %s', config)
            with open(config) as config:
                return dict(x.split(': ')
                            for x in config.read().strip().splitlines()
                            if not x.startswith('#'))
        except (IOError, OSError):
            continue
    return {}


async def main():
    """Main method."""
    if '-v' in argv:
        logging.basicConfig(level=logging.INFO)
    elif '-vv' in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.ERROR)

    async with Connection(**read_credentials()) as connection:
        if await connection.update():
            for vehicle in connection.vehicles:
                print(vehicle)


if __name__ == '__main__':
    asyncio.run(main())

#!/usr/bin/env python3


from datetime import datetime
from logging import Logger, getLogger
from typing import Dict, List, Optional

import arrow
from requests import Session

from electricitymap.contrib.lib.models.event_lists import ExchangeList
from electricitymap.contrib.lib.types import ZoneKey
from parsers.lib.exceptions import ParserException

# Useful links.
# https://en.wikipedia.org/wiki/Electricity_sector_in_Argentina
# https://en.wikipedia.org/wiki/List_of_power_stations_in_Argentina
# http://globalenergyobservatory.org/countryid/10#
# http://www.industcards.com/st-other-argentina.htm

# API Documentation: https://api.cammesa.com/demanda-svc/swagger-ui.html
CAMMESA_DEMANDA_ENDPOINT = (
    "https://api.cammesa.com/demanda-svc/generacion/ObtieneGeneracioEnergiaPorRegion/"
)
CAMMESA_EXCHANGE_ENDPOINT = (
    "https://api.cammesa.com/demanda-svc/demanda/IntercambioCorredoresGeo/"
)
CAMMESA_RENEWABLES_ENDPOINT = "https://cdsrenovables.cammesa.com/exhisto/RenovablesService/GetChartTotalTRDataSource/"

# Expected arrow direction if flow is first -> second
EXCHANGE_NAME_DIRECTION_MAPPING = {
    "AR->CL-SEN": ("ARG-CHI", 180),
    "AR->PY": ("ARG-PAR", 45),
    "AR->UY": ("ARG-URU", 0),
    "AR-NEA->BR-S": ("ARG-BRA", 45),
    "AR-BAS->AR-COM": ("PBA-COM", 225),
    "AR-CEN->AR-COM": ("CEN-COM", 270),
    "AR-CEN->AR-NOA": ("CEN-NOA", 135),
    "AR-CUY->AR-COM": ("CUY-COM", 315),
    "AR-CEN->AR-CUY": ("CEN-CUY", 225),
    "AR-LIT->AR-NEA": ("LIT-NEA", 45),
    "AR-BAS->AR-LIT": ("PBA-LIT", 90),
    "AR-CUY->AR-NOA": ("CUY-NOA", 45),
    "AR-LIT->AR-CEN": ("LIT-CEN", 225),
    "AR-LIT->AR-NOA": ("LIT-NOA", 135),
    "AR-BAS->AR-CEN": ("PBA-CEN", 135),
    "AR-COM->AR-PAT": ("COM-PAT", 270),
    "AR-NEA->AR-NOA": ("NEA-NOA", 180),
}

SOURCE = "cammesaweb.cammesa.com"


def fetch_production(
    zone_key="AR",
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
) -> List[dict]:
    """Requests up to date list of production mixes (in MW) of a given country."""

    if target_datetime:
        raise NotImplementedError("This parser is not yet able to parse past dates")

    current_session = session or Session()

    non_renewables_production: Dict[str, dict] = non_renewables_production_mix(
        zone_key, current_session
    )
    renewables_production: Dict[str, dict] = renewables_production_mix(
        zone_key, current_session
    )

    full_production_list = [
        {
            "datetime": arrow.get(datetime_tz_ar).to("UTC").datetime,
            "zoneKey": zone_key,
            "production": merged_production_mix(
                non_renewables_production[datetime_tz_ar],
                renewables_production[datetime_tz_ar],
            ),
            "capacity": {},
            "storage": {},
            "source": "cammesaweb.cammesa.com",
        }
        for datetime_tz_ar in non_renewables_production
        if datetime_tz_ar in renewables_production
    ]

    return full_production_list


def merged_production_mix(non_renewables_mix: dict, renewables_mix: dict) -> dict:
    """Merges production mix data from different sources. Hydro comes from two
    different sources that are added up."""

    production_mix = {
        "biomass": renewables_mix["biomass"],
        "solar": renewables_mix["solar"],
        "wind": renewables_mix["wind"],
        "hydro": non_renewables_mix["hydro"] + renewables_mix["hydro"],
        "nuclear": non_renewables_mix["nuclear"],
        "unknown": non_renewables_mix["unknown"],
    }

    return production_mix


def renewables_production_mix(zone_key: str, session: Session) -> Dict[str, dict]:
    """Retrieves production mix for renewables using CAMMESA's API"""

    today = arrow.now(tz="America/Argentina/Buenos_Aires").format("DD-MM-YYYY")
    params = {"desde": today, "hasta": today}
    renewables_response = session.get(CAMMESA_RENEWABLES_ENDPOINT, params=params)
    assert renewables_response.status_code == 200, (
        "Exception when fetching production for "
        "{}: error when calling url={} with payload={}".format(
            zone_key, CAMMESA_RENEWABLES_ENDPOINT, params
        )
    )

    production_list = renewables_response.json()
    sorted_production_list = sorted(production_list, key=lambda d: d["momento"])

    renewables_production: Dict[str, dict] = {
        production_info["momento"]: {
            "biomass": production_info["biocombustible"],
            "hydro": production_info["hidraulica"],
            "solar": production_info["fotovoltaica"],
            "wind": production_info["eolica"],
        }
        for production_info in sorted_production_list
    }

    return renewables_production


def non_renewables_production_mix(zone_key: str, session: Session) -> Dict[str, dict]:
    """Retrieves production mix for non renewables using CAMMESA's API"""

    params = {"id_region": 1002}
    api_cammesa_response = session.get(CAMMESA_DEMANDA_ENDPOINT, params=params)
    assert api_cammesa_response.status_code == 200, (
        "Exception when fetching production for "
        "{}: error when calling url={} with payload={}".format(
            zone_key, CAMMESA_DEMANDA_ENDPOINT, params
        )
    )

    production_list = api_cammesa_response.json()
    sorted_production_list = sorted(production_list, key=lambda d: d["fecha"])

    non_renewables_production: Dict[str, dict] = {
        production_info["fecha"]: {
            "hydro": production_info["hidraulico"],
            "nuclear": production_info["nuclear"],
            # As of 2022 thermal energy is mostly natural gas but
            # the data is not split. We put it into unknown for now.
            # More info: see page 21 in https://microfe.cammesa.com/static-content/CammesaWeb/download-manager-files/Sintesis%20Mensual/Informe%20Mensual_2021-12.pdf
            "unknown": production_info["termico"],
        }
        for production_info in sorted_production_list
    }

    return non_renewables_production


def fetch_exchange(
    zone_key1: ZoneKey,
    zone_key2: ZoneKey,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
) -> List[dict]:
    """Requests the last known power exchange (in MW) between two zones."""
    if target_datetime:
        raise NotImplementedError("This parser is not yet able to parse past dates")

    sorted_zone_keys = ZoneKey("->".join(sorted([zone_key1, zone_key2])))
    if sorted_zone_keys not in EXCHANGE_NAME_DIRECTION_MAPPING:
        raise ParserException(
            parser="AR.py",
            message="This exchange is not currently implemented",
            zone_key=sorted_zone_keys,
        )

    current_session = session or Session()

    api_cammesa_response = current_session.get(CAMMESA_EXCHANGE_ENDPOINT)
    if not api_cammesa_response.ok:
        raise ParserException(
            parser="AR.py",
            message=f"Exception when fetching exchange for {sorted_zone_keys}: error when calling url={CAMMESA_EXCHANGE_ENDPOINT}",
            zone_key=sorted_zone_keys,
        )
    exchange_name, expected_angle = EXCHANGE_NAME_DIRECTION_MAPPING[sorted_zone_keys]
    exchange_list = api_cammesa_response.json()["features"]
    exchange_data = next(
        (
            exchange["properties"]
            for exchange in exchange_list
            if exchange["properties"]["nombre"] == exchange_name
        ),
        None,
    )
    if exchange_data is None:
        raise ParserException(
            parser="AR.py",
            message=f"Exception when fetching exchange for {sorted_zone_keys}: exchange not found",
            zone_key=sorted_zone_keys,
        )
    given_angle = int(exchange_data["url"][6:])
    flow = int(exchange_data["text"])

    # inverse flow if arrow doesn't match expected direction
    if expected_angle != given_angle:
        flow = -flow
    exchange_datetime = datetime.fromisoformat(
        exchange_data["fecha"][:-2] + ":" + exchange_data["fecha"][-2:]
    )
    exchanges = ExchangeList(logger)
    exchanges.append(
        zoneKey=sorted_zone_keys,
        datetime=exchange_datetime,
        netFlow=flow,
        source=SOURCE,
    )
    return exchanges.to_list()


def fetch_price(
    zone_key: str = "AR",
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
) -> dict:
    """Requests the last known power price of a given country."""

    raise NotImplementedError("Fetching the price is not currently implemented")


if __name__ == "__main__":
    """Main method, never used by the Electricity Map backend, but handy for testing."""

    print("fetch_production() ->")
    print(fetch_production())
    print("fetch_exchange(AR, PY) ->")
    print(fetch_exchange(ZoneKey("AR"), ZoneKey("PY")))
    print("fetch_exchange(AR, UY) ->")
    print(fetch_exchange(ZoneKey("AR"), ZoneKey("UY")))
    print("fetch_exchange(AR, CL-SEN) ->")
    print(fetch_exchange(ZoneKey("AR"), ZoneKey("CL-SEN")))
    print("fetch_exchange(AR-CEN, AR-COM) ->")
    print(fetch_exchange(ZoneKey("AR-CEN"), ZoneKey("AR-COM")))
    print("fetch_exchange(AR-BAS, AR-LIT) ->")
    print(fetch_exchange(ZoneKey("AR-BAS"), ZoneKey("AR-LIT")))
    print("fetch_exchange(AR-COM, AR-PAT) ->")
    print(fetch_exchange(ZoneKey("AR-COM"), ZoneKey("AR-PAT")))

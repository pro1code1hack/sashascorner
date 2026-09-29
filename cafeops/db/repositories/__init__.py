"""SQLAlchemy repositories, one per aggregate, each implementing a protocol from
`protocols.py`.

The block below is the only thing that keeps `protocols.py` honest: every
`Sql*Repository` is assigned to its protocol under `TYPE_CHECKING`, so `mypy cafeops`
fails the moment an implementation and the contract disagree. It costs nothing at
runtime -- the block never executes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cafeops.db.repositories import protocols as _p
    from cafeops.db.repositories.agent_log import SqlAgentLogRepository
    from cafeops.db.repositories.batch import SqlBatchRepository
    from cafeops.db.repositories.channel import SqlChannelRepository
    from cafeops.db.repositories.checklist import SqlChecklistRepository
    from cafeops.db.repositories.composition import SqlCompositionRepository
    from cafeops.db.repositories.drift import SqlDriftRepository
    from cafeops.db.repositories.ingredient import SqlIngredientRepository
    from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
    from cafeops.db.repositories.par import SqlParLevelRepository
    from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
    from cafeops.db.repositories.sale import SqlSaleRepository
    from cafeops.db.repositories.season import SqlSeasonRepository
    from cafeops.db.repositories.sourcing import SqlSourcingRepository
    from cafeops.db.repositories.stock import SqlStockRepository
    from cafeops.db.repositories.supplier import SqlSupplierRepository

    def _conforms(session: Session) -> None:
        _SqlAgentLogRepository: _p.AgentLogRepository = SqlAgentLogRepository(session)
        _SqlBatchRepository: _p.BatchRepository = SqlBatchRepository(session)
        _SqlChannelRepository: _p.ChannelRepository = SqlChannelRepository(session)
        _SqlChecklistRepository: _p.ChecklistRepository = SqlChecklistRepository(session)
        _SqlCompositionRepository: _p.CompositionRepository = SqlCompositionRepository(session)
        _SqlDriftRepository: _p.DriftRepository = SqlDriftRepository(session)
        _SqlIngredientRepository: _p.IngredientRepository = SqlIngredientRepository(session)
        _SqlMenuCostRepository: _p.MenuCostRepository = SqlMenuCostRepository(session)
        _SqlParLevelRepository: _p.ParLevelRepository = SqlParLevelRepository(session)
        _SqlPurchaseOrderRepository: _p.PurchaseOrderRepository = SqlPurchaseOrderRepository(
            session
        )
        _SqlSaleRepository: _p.SaleRepository = SqlSaleRepository(session)
        _SqlSeasonRepository: _p.SeasonRepository = SqlSeasonRepository(session)
        _SqlSourcingRepository: _p.SourcingRepository = SqlSourcingRepository(session)
        _SqlStockRepository: _p.StockRepository = SqlStockRepository(session)
        _SqlSupplierRepository: _p.SupplierRepository = SqlSupplierRepository(session)

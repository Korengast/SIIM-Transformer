
#################################################
### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
#################################################
# file to edit: dev_nb/LearnerClass.ipynb

try:
    import sandesh
except:
    print('warning sandesh module not imported')
import torch
import torch.nn as nn
import torch.utils.data as D
import torch.nn.functional as F
import copy
import sys
import numpy as np
from torch.cuda.amp import autocast,GradScaler
from tqdm import notebook

class Learner(object):
    def __init__(self,model,optimizer,loss_func,name="",scheduler=None,device='cpu'):
        self.model=model
        self.optimizer=optimizer
        self.loss_func=loss_func
        self.scheduler=scheduler
        self.scaler=None
        self.device=device
        self.metric=None
        self.name=name
        self.log={}
        self.eth=0.99

    def init_amp(self):
        self.scaler=GradScaler()

    def get_y(self,batch):
        # get Y from Batch, the default is batch[-1] but you can overwrite it
        return batch[-1]

    def get_inds(self,batch):
        # get Y from Batch, the default is batch[-1] but you can overwrite it
        return batch[-1]

    def get_x(self,batch):
        # get x from Batch, the default is batch[:-1] but you can overwrite it
        if isinstance(batch,(list,tuple)):
            return batch[:-1]
        else:
            return [batch]

    def one_cycle(self,batch,train=True,do_step=True):
        device = self.device
        self.preprocess_batch(batch,train)
        y_true=self.get_y(batch)
        with autocast(self.scaler is not None):
            y_pred= self.model(*(x.to(device) for x in self.get_x(batch)))
            loss = self.loss_func(y_pred,y_true.to(device))
        if train:
            if self.scaler is not None:
                self.scaler.scale(loss).backward()
            else:
                loss.backward()
            if do_step:
                if self.scaler is not None:
                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                else:
                    self.optimizer.step()
                if self.scheduler is not None:
                    self.scheduler.step()
                self.optimizer.zero_grad()
        return loss.item() if train else (loss.item(), y_pred.to('cpu').detach())


    def one_training_epoch(self, dl, accumulation_steps=1):
        device = self.device
        torch.cuda.empty_cache()
        avg_loss = 0.
        lossf=0.
        self.model=self.model.train()
        self.model.zero_grad()
        tk0 = notebook.tqdm(dl)
        for i,batch in enumerate(tk0):
            do_step = (i+1) % accumulation_steps == 0
            loss_item = self.one_cycle(batch, train=True, do_step=do_step)
            e=min(self.eth,1-1.0/(i+1.0))
            lossf = e*lossf+(1-e)*loss_item
            tk0.set_postfix(loss = lossf)
            avg_loss += loss_item / len(dl)
        tk0.disable=False
        tk0.set_postfix(loss = avg_loss)
        tk0.disable=True
        return avg_loss

    def agg_tta(self,y):
        if y.shape[0]==1:
            return y[0]
        else:
            return y.mean(0)

    def preprocess_batch(self,batch,train=True):
        return(batch)

    def one_eval_epoch(self, dl,tta=1):
        device = self.device
        avg_loss = 0.
        avg_accuracy = 0.
        lossf=0
        self.model=self.model.eval()
        predss=[]
        with torch.no_grad():
            for t in range(tta):
                pred_list=[]
                true_list=[]
                tk0 = notebook.tqdm(dl)
                for i,batch in enumerate(tk0):
                    loss_item, y_pred = self.one_cycle(batch, train=False, do_step=False)
                    pred_list.append(y_pred.numpy())
                    true_list.append(self.get_y(batch).numpy())
                    e=min(self.eth,1-1.0/(i+1.0))
                    lossf = e*lossf+(1-e)*loss_item
                    tk0.set_postfix(loss = lossf)
                    avg_loss += loss_item / len(dl)
                y_true=np.concatenate(true_list,0)
                predss.append(np.concatenate(pred_list,0))
            preds=self.agg_tta(np.stack(predss,0)) if tta>1 else predss[0]
            m= dict() if self.metric is None else self.metric(preds,y_true)
        tk0.disable=False
        tk0.set_postfix(loss = avg_loss, **m)
        tk0.disable=True
        return avg_loss, m

    def send_log(self,**kwargs):
        log={'model':self.name}
        log.update(kwargs)
        if 'sandesh' in sys.modules:
            sandesh.send(log)
        else:
            print('log not sent - no sandesh module')

    def save2log(self,**kwargs):
        for key in kwargs.keys():
            if key not in self.log:
                self.log[key]=[]
            self.log[key].append(kwargs[key])

    def evaluate(self,ds,num_workers=8,tta=1,dl_args={'shuffle':False}):
            dl=D.DataLoader(ds,num_workers=num_workers,**dl_args)
            return self.one_eval_epoch(dl,tta=tta)


    def fit(self,num_epoches,train_ds,validate_ds=None,batch_size=None,lr=None,accumulation_steps=1,
            num_workers=8,send_log=True,eval_batch=None,reset_best=False,make_best=True,tta=1,
            train_dl_args={'shuffle':True},val_dl_args={'shuffle':False},save_checkpoint='best',path=''):
        if batch_size is not None:
            train_dl_args['batch_size']=batch_size
            val_dl_args['batch_size']=batch_size
        if eval_batch is not None:
            val_dl_args['batch_size']=eval_batch

        tq = notebook.tqdm(range(num_epoches))
        if lr is not None:
            self.set_lr(lr)
        if reset_best or not hasattr(self,'best_metric'):
            self.best_model=None
            self.best_metric=np.inf
        for k,epoch in enumerate(tq):
            self.on_epoch_begin(epoch,train_ds=train_ds,validate_ds=validate_ds)
            dl=D.DataLoader(train_ds, num_workers=num_workers,**train_dl_args)
            torch.cuda.empty_cache()
            tavg_loss=self.one_training_epoch(dl,accumulation_steps=accumulation_steps)
            if validate_ds is not None:
                avg_loss , metric =self.evaluate(validate_ds,
                                 num_workers=num_workers,dl_args=val_dl_args, tta=tta)
            else:
                avg_loss=tavg_loss
                metric={}
            if send_log:
                self.send_log(epoch=epoch,tloss=tavg_loss,loss=avg_loss,**metric)
            self.save2log(epoch=epoch,tloss=tavg_loss,loss=avg_loss,**metric)
            m = avg_loss  if 'metric' not in metric.keys() else metric['metric']
            if save_checkpoint=='last':
                self.save_checkpoint(path)
            if m<self.best_metric:
                self.best_metric=m
                self.best_model = copy.deepcopy(self.model.state_dict())
                tq.set_postfix(best_metric=self.best_metric)
                if save_checkpoint=='best':
                    self.save_checkpoint(path)
            self.on_epoch_end(epoch)

        print ('best metric:',self.best_metric)
        if make_best:
            self.model.load_state_dict(self.best_model)

    def save_model(self,path,name=None):
        name = self.name if name is None else name
        torch.save(self.model.state_dict(),f'{path}{name}')

    def load_model(self,path,name=None):
        name = self.name if name is None else name
        self.model.load_state_dict(torch.load(f'{path}{name}'))


    def save_checkpoint(self,path,name=None):
        name = self.name+'.chk' if name is None else name
        checkpoint={
                'model': self.model.state_dict(),
                'best_model': self.best_model,
                'best_metric': self.best_metric,
                'model': self.model.state_dict(),
                'optimizer': self.optimizer.state_dict(),
                'log' : self.log
                }
        if self.scaler:
            checkpoint['scaler']=self.scaler.state_dict()
        torch.save(checkpoint,f'{path}{name}')

    def load_checkpoint(self,path,name=None):
        name = self.name+'.chk' if name is None else name+'.chk'
        checkpoint=torch.load(f'{path}{name}')
        self.model.load_state_dict(checkpoint['model'])
        self.best_model=checkpoint['best_model']
        self.best_metric=checkpoint['best_metric']
        self.optimizer.load_state_dict(checkpoint['optimizer'])
        self.log=checkpoint['log']
        if 'scaler' in checkpoint.keys():
            self.scaler=GradScaler()
            self.scaler.load_state_dict(checkpoint['scaler'])
        else:
            self.scaler=None

    def set_lr(self,lr):
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = lr

    def on_epoch_begin(self,*args,**kargs):
        pass

    def on_epoch_end(self,*args,**kargs):
        pass

    def predict(self,ds,batch_size=None,num_workers=8,dl_args={'shuffle':False},return_inds=False,return_true=False,verbose=True,do_eval=True):
        device = self.device
        if batch_size is not None:
            dl_args['batch_size']=batch_size
        dl=D.DataLoader(ds,num_workers=num_workers,**dl_args)
        pred_list=[]
        inds_list=[]
        true_list=[]
        if do_eval:
            self.model=self.model.eval()
        with torch.no_grad():
            tk0 = notebook.tqdm(dl) if verbose else dl
            for i,batch in enumerate(tk0):
                with autocast(self.scaler is not None):
                    y_pred= self.model(*(x.to(device) for x in self.get_x(batch)))
                if return_inds:
                    inds_list.append(self.get_inds(batch).to('cpu').numpy())
                if return_true:
                    true_list.append(self.get_y(batch).to('cpu').numpy())
                pred_list.append(y_pred.to('cpu').numpy() if not isinstance(y_pred,tuple) else\
                                 tuple(y.to('cpu').numpy() for y in y_pred))
        pred = np.concatenate(pred_list,0) if not isinstance(pred_list[0],tuple) else\
                tuple(np.concatenate([p[i] for p in pred_list],0) for i in range(len(pred_list[0])))
        out=()
        if return_inds:
            out=out+(np.concatenate(inds_list,0),)
        if return_true:
            out=out+(np.concatenate(true_list,0),)

        return pred if len(out)==0 else (pred,)+out


"""
    DPN synapse

    Bittensor endpoint trained on PIL images to detect objects using DPN.
"""
import bittensor
from bittensor.utils.router import Router
from bittensor.synapse import Synapse
from bittensor.synapse import SynapseOutput
from bittensor.session import BTSession
from bittensor.utils.batch_transforms import Normalize

import argparse
from munch import Munch
import torch
import torch.nn as nn
import torch.nn.functional as F

class DPNSynapse(Synapse):
    """ Bittensor endpoint trained on PIL images to detect objects using DPN.
    """

    def __init__(   self, 
                    config: Munch,
                    session: BTSession,
                ):
        r""" Init a new DPN synapse module.

            Args:
                config (:obj: `munch.Munch`, `required`)
                    munch namespace config item.

                 session (:obj:`bittensor.Session`, `required`): 
                    bittensor session object. 
        """
        super(DPNSynapse, self).__init__(
            config = config,
            session = session)

        in_planes, out_planes = config.synapse.in_planes, config.synapse.out_planes
        num_blocks, dense_depth = config.synapse.num_blocks, config.synapse.dense_depth

        # Transform Network
        """ Transform network.
                Layers take in image inputs normalizes them and applies 
                4 convolutional layers. 
            Image encoder: transforms PIL-encoded tensors to a common shape.
            [batch_size, channels, rows, cols] -> [batch_size, -1, -1, -1] 

            Output: [batch_size, self.transform_dim (9728)]
        """
        self.transform = Normalize((0.1307,), (0.3081,), device=self.device)
        self.adaptive_pool = nn.AdaptiveAvgPool2d((32, 32))
        self.transform_conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.transform_bn1 = nn.BatchNorm2d(64)
        self.last_planes = 64
        self.transform_layer1 = self._make_layer(in_planes[0], out_planes[0], num_blocks[0], dense_depth[0], stride=1)
        self.transform_layer2 = self._make_layer(in_planes[1], out_planes[1], num_blocks[1], dense_depth[1], stride=2)
        self.transform_layer3 = self._make_layer(in_planes[2], out_planes[2], num_blocks[2], dense_depth[2], stride=1)
        self.transform_layer4 = self._make_layer(in_planes[3], out_planes[3], num_blocks[3], dense_depth[3], stride=2)
        self.transform_dim = (out_planes[3] * 4)+(((num_blocks[3]+1) * 4)*dense_depth[3])
        
        # Router object for training network connectivity.
        # [Transform] -> [ROUTER] -> [Neurons] -> [ROUTER]
        self.router = Router(x_dim = self.transform_dim , key_dim = 100, topk = 10)

        # Context layers.
        """
            Distillation model for remote context. This layer takes input 
            coming from transform layer, and runs it through 3 linear layers,
            projecting it to bittensor.__network_dim__.  
        """
        self.context_layer1 = nn.Linear(self.transform_dim, 512)
        self.context_layer2 = nn.Linear(512, 256)
        self.context_layer3 = nn.Linear(256, bittensor.__network_dim__)

        # hidden layer.
        self.hidden_layer1 = nn.Linear(self.transform_dim + bittensor.__network_dim__, 512)
        self.hidden_layer2 = nn.Linear(512, 256)
        self.hidden_layer3 = nn.Linear(256, bittensor.__network_dim__)

        # Layers to project target down to target size passed by config
        # (number of classes)
        self.target_layer1 = nn.Linear(bittensor.__network_dim__, 128)
        self.target_layer2 = nn.Linear(128, self.config.synapse.target_dim)

        self.to(self.device)

    @staticmethod
    def add_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        r""" This function adds the configuration items for the DPN synapse.
        These args are use to instantiate a Dual Path model. 
        Instantiating a configuration with the defaults will yield a "shallow" DPN-26 configuration. 

        For deeper network configurations, it is possible to set the num_blocks parameter to (3, 4, 20, 3) for a
        DPN-92. 
        
        For DPN-98 set the following:
            in_planes: (160, 320, 640, 1280)
            out_planes: (256, 512, 1024, 2048)
            num_blocks: (3, 6, 20, 3)
            dense_depth: (16, 32, 32, 128)
        """
        def to_list(arg):
            return [int(i) for i in arg.split(",")]
        parser.add_argument('--synapse.in_planes', default='160, 320, 640, 1280', action="append", type=to_list)
        parser.add_argument('--synapse.out_planes', default='256, 512, 1024, 2048', action="append", type=to_list)
        parser.add_argument('--synapse.num_blocks', default='3, 6, 20, 3', action="append", type=to_list)
        parser.add_argument('--synapse.dense_depth', default='16, 32, 32, 128', action="append", type=to_list)
        parser.add_argument('--synapse.target_dim', default=10, type=int, help='Final logit layer dimension. i.e. 10 for CIFAR-10.')
        parser.add_argument('--synapse.n_block_filter', default=100, type=int, help='Stale neurons are filtered after this many blocks.')
        return parser
    
    @staticmethod
    def check_config(config: Munch) -> Munch:
        assert isinstance(config.synapse.in_planes, list), 'synapse.in_planes must be a tuple, got {}'.format(config.synapse.in_planes)
        assert isinstance(config.synapse.out_planes, list), 'synapse.out_planes must be a tuple, got {}'.format(config.synapse.out_planes)
        assert isinstance(config.synapse.num_blocks, list), 'synapse.num_blocks must be a tuple, got {}'.format(config.synapse.num_blocks)
        assert isinstance(config.synapse.dense_depth, list), 'synapse.dense_depth must be a tuple, got {}'.format(config.synapse.dense_depth)
        assert all(isinstance(el, int) for el in config.synapse.in_planes), 'synapse.in_planes must be a tuple of ints, got {}'.format(config.synapse.in_planes)
        assert all(isinstance(el, int) for el in config.synapse.out_planes), 'synapse.out_planes must be a tuple of ints, got {}'.format(config.synapse.out_planes)
        assert all(isinstance(el, int) for el in config.synapse.num_blocks), 'synapse.num_blocks must be a tuple of ints, got {}'.format(config.synapse.num_blocks)
        assert all(isinstance(el, int) for el in config.synapse.dense_depth), 'synapse.dense_depth must be a tuple of ints, got {}'.format(config.synapse.dense_depth)
        return config
    
    def forward_image (     self,  
                            images: torch.Tensor):
        r""" Forward image inputs through the DPN synapse .

            Args:
                inputs (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_dim, channels, rows, cols)`, `required`): 
                    Image tensors produced by calling PIL.toTensor() and with sequence dimension.
            
            Returns:
                hidden (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_dim, bittensor.__network_dim__)`, `required`): 
                    Hidden layer encoding produced by using local_context.
        """
        # images: remove sequence dimension from images.
        # images.shape = [batch_size, channels, rows, cols] 
        images = images.view(images.shape[0] * images.shape[1], images.shape[2], images.shape[3], images.shape[4])

        # hidden: hidden layer using local context for local computation only.
        # hidden.shape = [batch_size, __network_dim__] 
        hidden = self.forward (images = images.to(self.device), remote = False).local_hidden
        
        # hidden: re-add sequence dimension to outputs.
        # hidden.shape = [batch_size, sequence_dim, __network_dim__] 
        hidden = torch.unsqueeze(hidden, 1)

        return hidden

    def call_remote(self, images, context):
        r""" Makes remote calls to neurons.

            Args:
                images (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, channels, rows, cols)`, `required`): 
                    Image tensors to send.
                
                context (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, transform_dim)`, `required`): 
                    Per example tensor used to select which neuron to send each example to.
            
            Returns:
                remote_context (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, bittensor.__network_dim__)`, `required`): 
                    Joined context vector from remote peer neurons.

                weights (:obj:`torch.LongTensor` of shape :obj:`(batch_size, metagraph.state.n)`, `optional`): 
                    weights for each active neuron.
        """
        # images: re-add sequence dimension to input images.
        # hidden.shape = [batch_size, sequence_dim, channels, rows, cols] 
        images = torch.unsqueeze(images, 1) 

        # uids: unique keys for peer neurons.
        # uids.shape = [metagraph.n]
        uids = self.session.metagraph.state.uids # Returns a list of neuron uids.
       
        # uids: uids with an emit call in the last 100 blocks.
        # uids = [-1]
        block = self.session.metagraph.state.block 
        emit = self.session.metagraph.state.emit
        staleness = (block - emit)
        uids = uids[torch.where(staleness > self.config.synapse.n_block_filter)] 

        # Return zeros if there are no remaining peer neurons.
        if torch.numel(uids) == 0:
            n = self.session.metagraph.state.n
            remote_context = torch.zeros(size=(images.shape[0], bittensor.__network_dim__))
            weights = torch.zeros(size=(images.shape[0], n))
            return remote_context, weights

        # neurons: endpoint information for filtered keys.
        # neurons.shape = [len(uids)]
        neurons = self.session.metagraph.state.uids_to_neurons(uids)
        
        # request: image inputs routeed to peers using context to filter topk.
        # request.shape = neurons.size * [-1, sequence_dim, channels, rows, cols]
        requests, weights = self.router.route( neurons, context, images ) 

        # responses: image responses from neurons.
        # responses.shape = neurons.size * [-1, sequence_dim, __network_dim__]
        responses = self.session.dendrite.forward_image( neurons, requests )

        # remote_context: Responses weighted and joined along the __network_dim__.
        # remote_context.shape = [batch_size, bittensor.__network_dim__]
        remote_context = self.router.join( responses )
        remote_context = remote_context.view(remote_context.shape[0] * remote_context.shape[1], remote_context.shape[2])

        # scatter weights back onto shape (bs, n)
        indices = self.session.metagraph.state.uids_to_indices(uids).repeat(images.shape[0], 1)
        filled_weights = torch.zeros(images.shape[0], self.session.metagraph.state.n)
        filled_weights.scatter_(1, indices, weights)
        return remote_context, filled_weights 

    
    def forward (   self,
                    images: torch.Tensor,
                    targets: torch.Tensor = None,
                    remote: bool = False):
        r""" Forward pass non-sequential image inputs and targets through the DPN Synapse.

            Args:
                images (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, channels, rows, cols)`, `required`): 
                    PIL.toTensor() encoded images.

                targets (:obj:`torch.FloatTensor`  of shape :obj:`(batch_size, config.target_size)`, `optional`): 
                    Image labels.

                remote (:obj:`bool')`, `optional`):
                    Switch between local and remote context. If true, function makes quries to the remote network.

            Returns:
                bittensor.SynapseOutput  (
                    loss  (:obj:`List[str]` of shape :obj:`(batch_size)`, `required`):
                        Total loss acumulation to be used by loss.backward()

                    local_hidden (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, bittensor.__network_dim__)`, `required`):
                        Hidden layer encoding produced by using local_context.

                    local_target (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, config.target_size)`, `optional`):
                        DPN Target predictions using local_context. 

                    local_target_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`): 
                        DPN Classification loss using local_context.

                    remote_hidden (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, bittensor.__network_dim__)`, `optional`): 
                        Hidden layer encoding produced using the remote_context.

                    remote_target (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, config.target_size)`, `optional`):
                        DPN Target predictions using the remote_context.

                    remote_target_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`):
                        DPN Classification loss using the remote_context.

                    distillation_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`): 
                        Distillation loss between local_context and remote_context.

                    weights (:obj:`torch.LongTensor` of shape :obj:`(batch_size, metagraph.state.n)`, `optional`): 
                        weights for each active neuron.
                )
        """
        # Return vars to be filled.
        output = SynapseOutput (loss = torch.tensor(0.0))
        r"""
            Transform the images into a common shape (32x32)
        """
        # transform: transform images to common shape.
        # transform.shape = [batch_size, self.transform_dim]
        transform = self.transform(images)
        transform = self.adaptive_pool(transform)
        transform = F.relu(self.transform_bn1(self.transform_conv1(transform.detach())))
        transform = self.transform_layer1(transform)
        transform = self.transform_layer2(transform)
        transform = self.transform_layer3(transform)
        transform = self.transform_layer4(transform)
        transform = F.avg_pool2d(transform, 4)
        transform = torch.flatten(transform, start_dim=1)

        # local_context: distillation model for remote_context.
        # local_context.shape = [batch_size, bittensor.__network_dim__]
        local_context = self.context_layer1(transform.detach())
        local_context = self.context_layer2(local_context)
        local_context = self.context_layer3(local_context)
        

        # local_hidden: hidden layer encoding using local_context.
        # local_hidden.shape = [batch_size, bittensor.__network_dim__]
        local_hidden = torch.cat([transform, local_context], dim=1)
        local_hidden = self.hidden_layer1(local_hidden)
        local_hidden = self.hidden_layer2(local_hidden)
        local_hidden = self.hidden_layer3(local_hidden)
        output.local_hidden = local_hidden
        
        if targets is not None:
            # local_target: projection of local_hidden onto target dimension.
            # local_target.shape = [batch_size, target_dim]
            targets.to(self.device)
            local_target = self.target_layer1(local_hidden)
            local_target = self.target_layer2(local_target)
            local_target = F.log_softmax(local_target, dim=1)
            output.local_target = local_target

            # local_target_loss: loss between local_target and passed targets.
            # local_target_loss.shape = [1]
            local_target_loss = F.nll_loss(local_target, targets)
            output.local_target_loss = local_target_loss
            output.loss = output.loss + local_target_loss
        
        if remote:
            output = self.forward_remote(local_context, output, images, transform, targets)

        return output

    def forward_remote(self, local_context, output, images, transform, targets):
        """  Forward pass non-sequential image inputs and targets through the remote context of the synapse.

        Args:
            local_context (:obj: `torch.FloatTensor` of shape :obj: `(batch_size, bittensor.__network_dim__)`, `required`)
                    Distillation model for remote_context.

            output (:obj: `Bittensor.SynapseOutput`, `required`)
                    The object containing the output thus far of the local context run

            images (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, channels, rows, cols)`, `required`): 
                    PIL.toTensor() encoded images.

            transform (:obj: `torch.FloatTensor` of shape :obj: `(batch_size, self.transform_dim)`, `required`):
                transform images to common shape.
            
            targets (:obj:`torch.FloatTensor`  of shape :obj:`(batch_size, target_dim)`, `optional`, defaults to None): 
                Image labels.

        Returns:
            bittensor.SynapseOutput ( 
                    loss  (:obj:`List[str]` of shape :obj:`(batch_size)`, `required`):
                        Total loss acumulation to be used by loss.backward()

                    local_hidden (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, bittensor.__network_dim__)`, `required`):
                        Hidden layer encoding produced using local_context.

                    local_target (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, target_dim)`, `optional`):
                        FFNN Target predictions using student_context. 

                    local_target_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`): 
                        FFNN Classification loss using student_context.

                    remote_hidden (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, bittensor.__network_dim__)`, `optional`): 
                        Hidden layer encoding produced using the remote_context.

                    remote_target (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, target_dim)`, `optional`):
                        FFNN Target predictions using the remote_context.

                    remote_target_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`):
                        FFNN Classification loss using the remote_context.

                    distillation_loss (:obj:`torch.FloatTensor` of shape :obj:`(1)`, `optional`): 
                        Distillation loss between local_context and remote_context.

                    keys (:obj:`torch.LongTensor` of shape :obj:`(-1)`, `optional`): 
                        Keys for queried neurons.

                    scores (:obj:`torch.LongTensor` of shape :obj:`(batch_size, len(keys))`, `optional`): 
                        scores for each active key per example.
                )
        """
        # remote_context: responses from a bittensor remote network call.
        # remote_context.shape = [batch_size, bittensor.__network_dim__]
        # make a remote call.
        remote_context, weights = self.call_remote(images, transform)
        output.weights = weights
        remote_context = remote_context.to(self.device)

        # distillation_loss: distillation loss between local_context and remote_context
        # distillation_loss.shape = [1]
        distillation_loss = F.mse_loss(local_context, remote_context.detach())
        output.distillation_loss = distillation_loss
        output.loss = output.loss + distillation_loss

        # remote_hidden: hidden layer encoding using remote_context.
        # remote_hidden.shape = [batch_size, bittensor.__network_dim__]
        remote_hidden = torch.cat([transform, remote_context], dim=1)
        remote_hidden = self.hidden_layer1(remote_hidden)
        remote_hidden = self.hidden_layer2(remote_hidden)
        remote_hidden = self.hidden_layer3(remote_hidden)
        output.remote_hidden = remote_hidden

        if targets is not None:
            # remote_target: projection of remote_hidden onto target dimension.
            # remote_target.shape = [batch_size, config.target_size]
            remote_target = self.target_layer1(remote_hidden)
            remote_target = self.target_layer2(remote_target)
            remote_target = F.log_softmax(remote_target, dim=1)
            output.remote_target = remote_target

            # remote_target_loss: loss between remote_target and passed targets.
            # remote_target_loss.shape = [1]
            remote_target_loss = F.nll_loss(remote_target, targets)
            output.loss = output.loss + remote_target_loss
            output.remote_target_loss = remote_target_loss
        
        return output

    def _make_layer(self, in_planes, out_planes, num_blocks, dense_depth, stride):
        """ Generates a sequential container containing Bottleneck layers.  

        Args:
            in_planes (tuple): 
                4-element tuple describing the in_planes config.

            out_planes (tuple): 
                4-element tuple describing the out_planes config.

            num_blocks (tuple): 
                4-element tuple describing the number of blocks at this layer.

            dense_depth (tuple): 
                4-element tuple describing the depth of this layer.
           
            stride (int): 
                Convolutional stride length.

        Returns:
            nn.Sequential: A torch.nn sequential container containing the layers outlined in the inputs.
        """
        strides = [stride] + [1]*(num_blocks-1)
        layers = []
        for i,stride in enumerate(strides):
            layers.append(self.Bottleneck(self.last_planes, in_planes, out_planes, dense_depth, stride, i==0))
            self.last_planes = out_planes + (i+2) * dense_depth
        return nn.Sequential(*layers)
    
    class Bottleneck(nn.Module):
        def __init__(self, last_planes, in_planes, out_planes, dense_depth, stride, first_layer):
            super(DPNSynapse.Bottleneck, self).__init__()
            self.out_planes = out_planes
            self.dense_depth = dense_depth

            self.conv1 = nn.Conv2d(last_planes, in_planes, kernel_size=1, bias=False)
            self.bn1 = nn.BatchNorm2d(in_planes)
            self.conv2 = nn.Conv2d(in_planes, in_planes, kernel_size=3, stride=stride, padding=1, groups=32, bias=False)
            self.bn2 = nn.BatchNorm2d(in_planes)
            self.conv3 = nn.Conv2d(in_planes, out_planes+dense_depth, kernel_size=1, bias=False)
            self.bn3 = nn.BatchNorm2d(out_planes + dense_depth)

            self.shortcut = nn.Sequential()
            if first_layer:
                self.shortcut = nn.Sequential(
                    nn.Conv2d(last_planes, out_planes + dense_depth, kernel_size=1, stride=stride, bias=False),
                    nn.BatchNorm2d(out_planes + dense_depth)
                )

        def forward(self, x):
            out = F.relu(self.bn1(self.conv1(x)))
            out = F.relu(self.bn2(self.conv2(out)))
            out = self.bn3(self.conv3(out))
            x = self.shortcut(x)
            d = self.out_planes
            out = torch.cat([x[:,:d,:,:]+out[:,:d,:,:], x[:,d:,:,:], out[:,d:,:,:]], 1)
            out = F.relu(out)
            return out